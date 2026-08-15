from typing import Any, List, Optional
from pydantic import BaseModel, Field


class UIRNode(BaseModel):
    """Base class for all language-agnostic intermediate syntax nodes."""
    node_id: str
    raw_text: str


class AssignmentNode(UIRNode):
    """Represent an assignment expression statement (e.g. variable = expression)."""
    target: str
    value_expression: str


class FunctionDeclNode(UIRNode):
    """Represent a function or class method declaration."""
    name: str
    parameters: List[str] = Field(default_factory=list)
    return_type: str = "void"
    body_nodes: List[UIRNode] = Field(default_factory=list)


class CallNode(UIRNode):
    """Represent a function or method invocation statement."""
    callee: str
    arguments: List[str] = Field(default_factory=list)


class BranchNode(UIRNode):
    """Represent conditional control statements (e.g., if/else blocks)."""
    condition: str
    true_path: List[UIRNode] = Field(default_factory=list)
    false_path: List[UIRNode] = Field(default_factory=list)


class ReturnNode(UIRNode):
    """Represent value return statements."""
    expression: Optional[str] = None
