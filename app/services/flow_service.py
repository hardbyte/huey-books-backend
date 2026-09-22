"""
Flow Service - Domain service for flow management operations.

This service follows the established architecture patterns in the codebase:
- Uses FlowRepository interface for data access
- Integrates with existing EventOutboxService for reliable event delivery
- Raises service-layer exceptions (not HTTP exceptions)
- Handles complex flow workflows with proper orchestration
- Follows the established service layer patterns like CMSWorkflowService
"""

from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from structlog import get_logger

from app.models.cms import FlowConnection, FlowDefinition, FlowNode
from app.repositories.flow_repository import FlowRepository, FlowRepositoryImpl
from app.schemas.cms import (
    ConnectionCreate,
    FlowCloneRequest,
    FlowCreate,
    FlowUpdate,
    NodeCreate,
    NodeUpdate,
)
from app.services.event_outbox_service import EventOutboxService
from app.services.exceptions import (
    CMSWorkflowError,
    FlowNotFoundError,
    FlowValidationError,
)

logger = get_logger()


class FlowService:
    """
    Write service for Flow operations following established patterns.

    This service implements the same architecture as CMSWorkflowService:
    - Uses domain-specific repository interfaces
    - Integrates with EventOutboxService for reliable events
    - Maintains transaction boundaries for write operations
    - Contains business logic for complex flow workflows
    - Raises domain exceptions for proper error handling
    """

    def __init__(
        self,
        flow_repo: Optional[FlowRepository] = None,
        event_outbox: Optional[EventOutboxService] = None,
    ):
        # Dependency injection for testability
        self.flow_repo = flow_repo or FlowRepositoryImpl()
        self.event_outbox = event_outbox or EventOutboxService()

    # Flow Management Methods

    async def list_flows_with_filters(
        self,
        db: AsyncSession,
        published: Optional[bool] = None,
        active: Optional[bool] = None,
        search: Optional[str] = None,
        version: Optional[str] = None,
        skip: int = 0,
        limit: int = 100,
    ) -> Tuple[List[FlowDefinition], int]:
        """
        List flows with filters and return both data and total count.

        This method provides business logic for flow listing with proper
        filtering and pagination.
        """
        try:
            # Get both flows and count in parallel for efficiency
            flows = await self.flow_repo.find_flows_with_filters(
                db,
                published=published,
                active=active,
                search=search,
                version=version,
                skip=skip,
                limit=limit,
            )

            total_count = await self.flow_repo.count_flows_with_filters(
                db, published=published, active=active, search=search, version=version
            )

            logger.debug(
                "Listed flows with business logic",
                count=len(flows),
                total=total_count,
                published=published,
                active=active,
                search=search,
            )

            return flows, total_count

        except Exception as e:
            logger.error("Failed to list flows", error=str(e))
            raise CMSWorkflowError(f"Failed to list flows: {str(e)}")

    async def get_flow_by_id(self, db: AsyncSession, flow_id: UUID) -> FlowDefinition:
        """
        Get flow by ID with proper error handling.

        Raises FlowNotFoundError if flow doesn't exist.
        """
        try:
            flow = await self.flow_repo.get_flow_by_id(db, flow_id)
            if not flow:
                raise FlowNotFoundError(str(flow_id))

            logger.debug("Retrieved flow by ID", flow_id=flow_id, name=flow.name)
            return flow

        except FlowNotFoundError:
            raise
        except Exception as e:
            logger.error("Failed to get flow", flow_id=flow_id, error=str(e))
            raise CMSWorkflowError(f"Failed to get flow: {str(e)}")

    async def get_flow_with_components(
        self, db: AsyncSession, flow_id: UUID
    ) -> FlowDefinition:
        """
        Get flow with all nodes and connections loaded.

        This is useful for operations that need the complete flow structure.
        """
        try:
            flow = await self.flow_repo.get_flow_with_components(db, flow_id)
            if not flow:
                raise FlowNotFoundError(str(flow_id))

            logger.debug(
                "Retrieved flow with components",
                flow_id=flow_id,
                nodes=len(flow.nodes),
                connections=len(flow.connections),
            )
            return flow

        except FlowNotFoundError:
            raise
        except Exception as e:
            logger.error(
                "Failed to get flow with components", flow_id=flow_id, error=str(e)
            )
            raise CMSWorkflowError(f"Failed to get flow with components: {str(e)}")

    async def create_flow(
        self, db: AsyncSession, flow_data: FlowCreate, created_by: Optional[UUID] = None
    ) -> FlowDefinition:
        """
        Create new flow with business logic validation.

        Performs validation and emits events for flow creation.
        """
        try:
            requested_publish = bool(flow_data.is_published)

            # Create the flow
            create_payload = flow_data.model_copy(update={"is_published": False})
            flow = await self.flow_repo.create_flow(db, create_payload, created_by)

            # Materialize nodes and connections from provided snapshot (if any)
            try:
                from app.services.flow_snapshot import materialize_snapshot

                await materialize_snapshot(db, flow.id, flow_data.flow_data or {})
                await self._regenerate_flow_data(db, flow.id, commit=False)
            except Exception as e:
                logger.warning(
                    "Failed to materialize flow graph from flow_data", error=str(e)
                )

            # Emit flow created event
            await self._emit_flow_event(
                db,
                "flow_created",
                flow.id,
                {
                    "flow_name": flow.name,
                    "created_by": str(created_by) if created_by else None,
                    "version": flow.version,
                },
            )
            # Ensure outbox event persists
            await db.commit()

            logger.info(
                "Created flow with business logic",
                flow_id=flow.id,
                name=flow.name,
                created_by=created_by,
            )

            if requested_publish:
                flow = await self.publish_flow(db, flow.id, created_by, None)

            return flow

        except Exception as e:
            logger.error(
                "Failed to create flow", error=str(e), flow_name=flow_data.name
            )
            raise CMSWorkflowError(f"Failed to create flow: {str(e)}")

    async def update_flow(
        self,
        db: AsyncSession,
        flow_id: UUID,
        update_data: FlowUpdate,
        published_by: Optional[UUID] = None,
    ) -> FlowDefinition:
        """
        Update flow with business validation.

        Validates the flow exists and performs the update with event emission.
        """
        try:
            # Ensure flow exists
            await self.get_flow_by_id(db, flow_id)

            update_payload = update_data.model_dump(exclude_unset=True)
            publish_flag = update_payload.pop("publish", None)
            is_published_flag = update_payload.pop("is_published", None)
            if publish_flag is None:
                publish_flag = is_published_flag

            flow_data_payload = update_payload.get("flow_data")
            filtered_update = FlowUpdate.model_validate(update_payload)

            # Update the flow
            updated_flow = await self.flow_repo.update_flow(
                db, flow_id, filtered_update
            )

            if flow_data_payload is not None:
                from app.services.flow_snapshot import materialize_snapshot

                await materialize_snapshot(db, flow_id, flow_data_payload or {})
                await self._regenerate_flow_data(db, flow_id, commit=False)

            # Emit flow updated event only if we changed actual fields
            if update_payload:
                await self._emit_flow_event(
                    db,
                    "flow_updated",
                    flow_id,
                    {
                        "flow_name": updated_flow.name,
                        "changes": update_payload,
                    },
                )

            if publish_flag is not None:
                if publish_flag:
                    await self._validate_flow_for_publishing(db, flow_id)
                    updated_flow = await self.flow_repo.publish_flow(
                        db, flow_id, published_by, None
                    )
                    await self._emit_flow_event(
                        db,
                        "flow_published",
                        flow_id,
                        {
                            "flow_name": updated_flow.name,
                            "published_by": str(published_by) if published_by else None,
                            "version": updated_flow.version,
                        },
                    )
                else:
                    updated_flow = await self.flow_repo.unpublish_flow(db, flow_id)
                    await self._emit_flow_event(
                        db,
                        "flow_unpublished",
                        flow_id,
                        {"flow_name": updated_flow.name},
                    )

            await db.commit()

            logger.info(
                "Updated flow with business logic",
                flow_id=flow_id,
                name=updated_flow.name,
            )

            return updated_flow

        except FlowNotFoundError:
            raise
        except Exception as e:
            logger.error("Failed to update flow", flow_id=flow_id, error=str(e))
            raise CMSWorkflowError(f"Failed to update flow: {str(e)}")

    async def clone_flow(
        self,
        db: AsyncSession,
        source_flow_id: UUID,
        clone_request: FlowCloneRequest,
        created_by: Optional[UUID] = None,
    ) -> FlowDefinition:
        """
        Clone flow with all its components and business logic.

        Creates a complete copy of the source flow including nodes and connections.
        """
        try:
            # Get source flow to ensure it exists
            source_flow = await self.get_flow_by_id(db, source_flow_id)

            # Clone the flow
            cloned_flow = await self.flow_repo.clone_flow(
                db,
                source_flow,
                clone_request.name,
                created_by,
                clone_request.description,
                clone_request.version,
                info_override=clone_request.info,
                clone_nodes=bool(clone_request.clone_nodes),
                clone_connections=bool(clone_request.clone_connections),
            )

            # Emit flow cloned event
            await self._emit_flow_event(
                db,
                "flow_cloned",
                cloned_flow.id,
                {
                    "source_flow_id": str(source_flow_id),
                    "source_flow_name": source_flow.name,
                    "cloned_flow_name": cloned_flow.name,
                    "created_by": str(created_by) if created_by else None,
                },
            )
            # Ensure outbox event persists
            await db.commit()

            logger.info(
                "Cloned flow with business logic",
                source_id=source_flow_id,
                cloned_id=cloned_flow.id,
                new_name=clone_request.name,
            )

            return cloned_flow

        except FlowNotFoundError:
            raise
        except Exception as e:
            logger.error(
                "Failed to clone flow", source_flow_id=source_flow_id, error=str(e)
            )
            raise CMSWorkflowError(f"Failed to clone flow: {str(e)}")

    async def publish_flow(
        self,
        db: AsyncSession,
        flow_id: UUID,
        published_by_user_id: Optional[UUID],
        version: Optional[str] = None,
    ) -> FlowDefinition:
        """
        Publish flow with validation and workflow logic.

        Performs comprehensive validation before publishing and emits events.
        """
        try:
            # Validate flow exists and is ready for publishing
            await self._validate_flow_for_publishing(db, flow_id)

            # Publish the flow
            published_flow = await self.flow_repo.publish_flow(
                db, flow_id, published_by_user_id, version
            )

            # Emit flow published event
            await self._emit_flow_event(
                db,
                "flow_published",
                flow_id,
                {
                    "flow_name": published_flow.name,
                    "published_by": str(published_by_user_id)
                    if published_by_user_id
                    else None,
                    "version": published_flow.version,
                },
            )
            # Ensure outbox event persists
            await db.commit()

            logger.info(
                "Published flow with business logic",
                flow_id=flow_id,
                published_by=published_by_user_id,
                version=published_flow.version,
            )

            return published_flow

        except (FlowNotFoundError, FlowValidationError):
            raise
        except Exception as e:
            logger.error("Failed to publish flow", flow_id=flow_id, error=str(e))
            raise CMSWorkflowError(f"Failed to publish flow: {str(e)}")

    async def unpublish_flow(self, db: AsyncSession, flow_id: UUID) -> FlowDefinition:
        """
        Unpublish flow with business logic.
        """
        try:
            # Ensure flow exists
            await self.get_flow_by_id(db, flow_id)

            # Unpublish the flow
            unpublished_flow = await self.flow_repo.unpublish_flow(db, flow_id)

            # Emit flow unpublished event
            await self._emit_flow_event(
                db, "flow_unpublished", flow_id, {"flow_name": unpublished_flow.name}
            )
            # Ensure outbox event persists
            await db.commit()

            logger.info("Unpublished flow", flow_id=flow_id)

            return unpublished_flow

        except FlowNotFoundError:
            raise
        except Exception as e:
            logger.error("Failed to unpublish flow", flow_id=flow_id, error=str(e))
            raise CMSWorkflowError(f"Failed to unpublish flow: {str(e)}")

    async def soft_delete_flow(self, db: AsyncSession, flow_id: UUID) -> FlowDefinition:
        """
        Soft delete flow with business logic.

        Sets is_active=False and emits appropriate events.
        """
        try:
            # Ensure flow exists
            await self.get_flow_by_id(db, flow_id)

            # Soft delete the flow
            deleted_flow = await self.flow_repo.soft_delete_flow(db, flow_id)

            # Emit flow deleted event
            await self._emit_flow_event(
                db, "flow_soft_deleted", flow_id, {"flow_name": deleted_flow.name}
            )
            # Ensure outbox event persists
            await db.commit()

            logger.info("Soft deleted flow", flow_id=flow_id)

            return deleted_flow

        except FlowNotFoundError:
            raise
        except Exception as e:
            logger.error("Failed to soft delete flow", flow_id=flow_id, error=str(e))
            raise CMSWorkflowError(f"Failed to soft delete flow: {str(e)}")

    # Node Management Methods
    async def list_nodes(
        self,
        db: AsyncSession,
        flow_id: UUID,
        skip: int = 0,
        limit: int = 100,
    ) -> Tuple[List[FlowNode], int]:
        try:
            await self.get_flow_by_id(db, flow_id)
            nodes = await self.flow_repo.get_nodes_by_flow(db, flow_id, skip, limit)
            total = await self.flow_repo.count_nodes_by_flow(db, flow_id)
            return nodes, total
        except Exception as e:
            logger.error("Failed to list nodes", flow_id=flow_id, error=str(e))
            raise CMSWorkflowError(f"Failed to list nodes: {str(e)}")

    async def get_node(
        self, db: AsyncSession, flow_id: UUID, node_db_id: UUID
    ) -> FlowNode | None:
        try:
            node = await self.flow_repo.get_node_by_db_id(db, node_db_id)
            if not node or node.flow_id != flow_id:
                return None
            return node
        except Exception as e:
            logger.error("Failed to get node", node_id=node_db_id, error=str(e))
            raise CMSWorkflowError(f"Failed to get node: {str(e)}")

    async def create_node(
        self, db: AsyncSession, flow_id: UUID, node_data: NodeCreate
    ) -> FlowNode:
        """Create node with flow validation."""
        try:
            # Ensure flow exists
            await self.get_flow_by_id(db, flow_id)

            # Create the node
            node = await self.flow_repo.create_node(db, flow_id, node_data)

            await self._regenerate_flow_data(db, flow_id, commit=False)

            logger.info("Created node", node_id=node.id, flow_id=flow_id)

            await db.commit()
            return node

        except FlowNotFoundError:
            raise
        except Exception as e:
            logger.error("Failed to create node", flow_id=flow_id, error=str(e))
            raise CMSWorkflowError(f"Failed to create node: {str(e)}")

    async def update_node(
        self, db: AsyncSession, node_id: UUID, update_data: NodeUpdate
    ) -> FlowNode:
        """Update node with validation."""
        try:
            # Ensure node exists for proper 404 semantics
            existing = await self.flow_repo.get_node_by_db_id(db, node_id)
            if not existing:
                raise CMSWorkflowError("Node not found")
            # Update the node
            updated_node = await self.flow_repo.update_node(db, node_id, update_data)

            await self._regenerate_flow_data(db, existing.flow_id, commit=False)

            logger.info("Updated node", node_id=node_id)

            await db.commit()
            return updated_node

        except Exception as e:
            logger.error("Failed to update node", node_id=node_id, error=str(e))
            raise CMSWorkflowError(f"Failed to update node: {str(e)}")

    async def delete_node(self, db: AsyncSession, node_id: UUID) -> bool:
        """Delete node and its connections."""
        try:
            existing = await self.flow_repo.get_node_by_db_id(db, node_id)
            if not existing:
                logger.warning("Node not found for deletion", node_id=node_id)
                return False

            # Delete the node and its connections
            deleted = await self.flow_repo.delete_node_with_connections(db, node_id)

            if deleted:
                logger.info("Deleted node with connections", node_id=node_id)

            if deleted:
                await self._regenerate_flow_data(db, existing.flow_id, commit=False)
                await db.commit()
            return deleted

        except Exception as e:
            logger.error("Failed to delete node", node_id=node_id, error=str(e))
            raise CMSWorkflowError(f"Failed to delete node: {str(e)}")

    async def update_node_positions(
        self,
        db: AsyncSession,
        flow_id: UUID,
        positions: Dict[str, Any],
    ) -> None:
        try:
            await self.get_flow_by_id(db, flow_id)
            await self.flow_repo.update_node_positions(db, flow_id, positions)
            await self._emit_flow_event(
                db,
                "flow_node_positions_updated",
                flow_id,
                {"positions_count": len(positions)},
            )
            await self._regenerate_flow_data(db, flow_id, commit=False)
            await db.commit()
        except Exception as e:
            logger.error(
                "Failed to update node positions", flow_id=flow_id, error=str(e)
            )
            raise CMSWorkflowError(f"Failed to update node positions: {str(e)}")

    # Snapshot regeneration orchestration
    async def _regenerate_flow_data(
        self, db: AsyncSession, flow_id: UUID, *, commit: bool = True
    ) -> FlowDefinition:
        """Regenerate flow.flow_data using FlowSnapshotBuilder."""
        from sqlalchemy import select

        from app.models.cms import FlowDefinition
        from app.services.flow_snapshot import build_snapshot_from_db

        result = await db.execute(
            select(FlowDefinition).where(FlowDefinition.id == flow_id)
        )
        flow = result.scalar_one()
        snapshot = await build_snapshot_from_db(
            db, flow_id, preserve=flow.flow_data or {}
        )
        flow.flow_data = snapshot
        if commit:
            await db.commit()
            await db.refresh(flow)
        return flow

    async def regenerate_all_flow_data(
        self, db: AsyncSession, flow_ids: Optional[List[UUID]] = None
    ) -> Dict[str, Any]:
        """Regenerate flow_data for all (or specified) flows. Returns stats."""
        from time import perf_counter

        from sqlalchemy import select

        from app.models.cms import FlowDefinition

        started = perf_counter()
        # Determine which flows to process
        if flow_ids:
            ids = flow_ids
        else:
            result = await db.execute(select(FlowDefinition.id))
            ids = list(result.scalars().all())

        updated = 0
        errors: List[Dict[str, Any]] = []
        per_flow: List[Dict[str, Any]] = []
        for fid in ids:
            try:
                t0 = perf_counter()
                await self._regenerate_flow_data(db, fid)
                updated += 1
                per_flow.append(
                    {
                        "flow_id": str(fid),
                        "duration_ms": int((perf_counter() - t0) * 1000),
                    }
                )
            except Exception as e:
                logger.warning(
                    "Failed to regenerate flow_data", flow_id=str(fid), error=str(e)
                )
                errors.append({"flow_id": str(fid), "error": str(e)})

        total_ms = int((perf_counter() - started) * 1000)
        stats = {
            "requested": len(ids),
            "updated": updated,
            "errors": errors,
            "duration_ms": total_ms,
            "per_flow": per_flow,
        }
        logger.info("flow_snapshot_regeneration", **stats)
        return stats

    # Connection Management Methods

    async def create_connection(
        self, db: AsyncSession, flow_id: UUID, connection_data: ConnectionCreate
    ) -> FlowConnection:
        """Create connection with flow validation."""
        try:
            # Ensure flow exists
            await self.get_flow_by_id(db, flow_id)

            # Create the connection
            connection = await self.flow_repo.create_connection(
                db, flow_id, connection_data
            )

            await self._regenerate_flow_data(db, flow_id, commit=False)

            logger.info(
                "Created connection", connection_id=connection.id, flow_id=flow_id
            )

            await db.commit()
            return connection

        except FlowNotFoundError:
            raise
        except Exception as e:
            logger.error("Failed to create connection", flow_id=flow_id, error=str(e))
            raise CMSWorkflowError(f"Failed to create connection: {str(e)}")

    async def delete_connection(self, db: AsyncSession, connection_id: UUID) -> bool:
        """Delete connection by ID."""
        try:
            existing = await self.flow_repo.get_connection_by_id(db, connection_id)
            if not existing:
                logger.warning(
                    "Connection not found for deletion", connection_id=connection_id
                )
                return False

            # Delete the connection
            deleted = await self.flow_repo.delete_connection(db, connection_id)

            if deleted:
                logger.info("Deleted connection", connection_id=connection_id)

            if deleted:
                await self._regenerate_flow_data(db, existing.flow_id, commit=False)
                await db.commit()
            return deleted

        except Exception as e:
            logger.error(
                "Failed to delete connection", connection_id=connection_id, error=str(e)
            )
            raise CMSWorkflowError(f"Failed to delete connection: {str(e)}")

    async def list_connections(
        self,
        db: AsyncSession,
        flow_id: UUID,
        skip: int = 0,
        limit: int = 100,
    ) -> Tuple[List[FlowConnection], int]:
        try:
            await self.get_flow_by_id(db, flow_id)
            conns = await self.flow_repo.get_connections_by_flow(
                db, flow_id, skip, limit
            )
            total = await self.flow_repo.count_connections_by_flow(db, flow_id)
            return conns, total
        except Exception as e:
            logger.error("Failed to list connections", flow_id=flow_id, error=str(e))
            raise CMSWorkflowError(f"Failed to list connections: {str(e)}")

    async def get_connection_by_id(
        self, db: AsyncSession, connection_id: UUID
    ) -> FlowConnection | None:
        try:
            return await self.flow_repo.get_connection_by_id(db, connection_id)
        except Exception as e:
            logger.error(
                "Failed to get connection", connection_id=connection_id, error=str(e)
            )
            raise CMSWorkflowError(f"Failed to get connection: {str(e)}")

    # Private Helper Methods

    async def _validate_flow_for_publishing(
        self, db: AsyncSession, flow_id: UUID
    ) -> None:
        """
        Validate flow is ready for publishing.

        Performs comprehensive validation including structure checks.
        """
        # Get flow with components for validation
        flow = await self.get_flow_with_components(db, flow_id)

        validation_errors = []

        # Basic validation - ensure there is at least one node
        has_db_nodes = bool(flow.nodes)
        has_json_nodes = bool(flow.flow_data and flow.flow_data.get("nodes"))
        if not has_db_nodes and not has_json_nodes:
            validation_errors.append("Flow must have at least one node")

        # Validate entry_node_id exists either in DB nodes or in JSON nodes
        entry_id = flow.entry_node_id
        if not entry_id:
            validation_errors.append("Flow must have an entry_node_id")
        else:
            entry_in_db = any(n.node_id == entry_id for n in (flow.nodes or []))
            entry_in_json = any(
                (n.get("node_id") == entry_id or n.get("id") == entry_id)
                for n in (flow.flow_data or {}).get("nodes", [])
            )
            if not (entry_in_db or entry_in_json):
                validation_errors.append("Entry node id does not exist in flow")

        # Additional business logic validations can be added here

        if validation_errors:
            logger.warning(
                "Flow validation failed", flow_id=flow_id, errors=validation_errors
            )
            raise FlowValidationError(validation_errors)

        logger.debug("Flow validation passed", flow_id=flow_id)

    async def _emit_flow_event(
        self,
        db: AsyncSession,
        event_type: str,
        flow_id: UUID,
        event_data: Dict[str, Any],
    ) -> None:
        """
        Emit flow-related events through the event outbox.

        This ensures reliable event delivery following the existing pattern.
        """
        try:
            # Include aggregate_id in the payload rather than as a separate parameter
            payload_with_id = {**event_data, "aggregate_id": str(flow_id)}

            await self.event_outbox.publish_event(
                db=db,
                event_type=event_type,
                destination="audit:flow",
                payload=payload_with_id,
            )

            logger.debug("Emitted flow event", event_type=event_type, flow_id=flow_id)

        except Exception as e:
            # Don't fail the main operation for event emission issues
            # but log the error for monitoring
            logger.error(
                "Failed to emit flow event",
                event_type=event_type,
                flow_id=flow_id,
                error=str(e),
            )
