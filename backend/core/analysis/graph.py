import asyncio
import logging
from typing import Any, Callable, Dict, List, Set
from pydantic import BaseModel, Field

logger = logging.getLogger("backend.analysis.graph")


class ExecutionNode(BaseModel):
    """Represent an individual executable node inside the analysis DAG."""
    node_id: str
    dependencies: List[str] = Field(default_factory=list)
    priority: int = 1
    status: str = "PENDING"  # PENDING, RUNNING, COMPLETED, FAILED
    retry_limit: int = 3
    timeout_seconds: float = 30.0

    class Config:
        arbitrary_types_allowed = True


class ExecutionGraph:
    """Directed Acyclic Graph (DAG) executor orchestrating independent task dependencies."""

    def __init__(self):
        self.nodes: Dict[str, ExecutionNode] = {}
        self.tasks: Dict[str, Callable] = {}
        self.completed_nodes: Set[str] = set()

    def add_node(self, node: ExecutionNode, task_func: Callable):
        self.nodes[node.node_id] = node
        self.tasks[node.node_id] = task_func

    def _topological_sort(self) -> List[str]:
        visited = set()
        temp = set()
        order = []

        def visit(node_id):
            if node_id in temp:
                raise ValueError(f"Circular dependency detected in execution graph: {node_id}")
            if node_id not in visited:
                temp.add(node_id)
                node = self.nodes.get(node_id)
                if node:
                    for dep in node.dependencies:
                        visit(dep)
                temp.remove(node_id)
                visited.add(node_id)
                order.append(node_id)

        for n_id in self.nodes:
            if n_id not in visited:
                visit(n_id)
        return order

    async def execute_async(self, *args, **kwargs) -> Dict[str, Any]:
        """Execute the DAG topologically. Independent nodes run concurrently."""
        order = self._topological_sort()
        logger.info(f"Starting DAG execution. Topological sequence: {order}")

        results = {}
        # Simple task queue executor running concurrent leaves
        pending = set(self.nodes.keys())
        
        while pending:
            # Find nodes that have all dependencies completed
            runnable = []
            for n_id in pending:
                node = self.nodes[n_id]
                if all(dep in self.completed_nodes for dep in node.dependencies):
                    runnable.append(n_id)

            if not runnable:
                if pending:
                    raise RuntimeError("Deadlock detected or unresolved dependencies in DAG.")
                break

            # Execute runnable nodes in parallel
            logger.info(f"Running concurrent DAG nodes: {runnable}")
            tasks = []
            for n_id in runnable:
                node = self.nodes[n_id]
                node.status = "RUNNING"
                tasks.append(self._run_node_with_retry(node, *args, **kwargs))

            node_results = await asyncio.gather(*tasks, return_exceptions=True)

            for n_id, res in zip(runnable, node_results):
                node = self.nodes[n_id]
                if isinstance(res, Exception):
                    logger.error(f"DAG Node '{n_id}' failed: {res}")
                    node.status = "FAILED"
                    raise res
                else:
                    node.status = "COMPLETED"
                    self.completed_nodes.add(n_id)
                    results[n_id] = res
                    pending.remove(n_id)

        return results

    async def _run_node_with_retry(self, node: ExecutionNode, *args, **kwargs):
        task_func = self.tasks[node.node_id]
        retries = 0
        while retries < node.retry_limit:
            try:
                # Wrap inside a timeout execution
                return await asyncio.wait_for(task_func(*args, **kwargs), timeout=node.timeout_seconds)
            except asyncio.TimeoutError:
                retries += 1
                logger.warning(f"Timeout executing node '{node.node_id}' (attempt {retries}/{node.retry_limit})")
            except Exception as e:
                retries += 1
                logger.warning(f"Error executing node '{node.node_id}' (attempt {retries}/{node.retry_limit}): {e}")
                if retries >= node.retry_limit:
                    raise e
        raise TimeoutError(f"Node '{node.node_id}' execution timed out after multiple retries.")
