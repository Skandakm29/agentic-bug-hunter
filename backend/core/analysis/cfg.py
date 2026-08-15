import re
from typing import Dict, List, Set
from pydantic import BaseModel, Field


class BasicBlock(BaseModel):
    """Represent an uninterrupted block of instructions ending with a branch."""
    block_id: str
    statements: List[str] = Field(default_factory=list)
    successors: List[str] = Field(default_factory=list)
    predecessors: List[str] = Field(default_factory=list)


class CFGGraph(BaseModel):
    """Represent the complete control flow structure within a function body."""
    blocks: Dict[str, BasicBlock] = Field(default_factory=dict)
    entry_block: str = "B_ENTRY"
    exit_block: str = "B_EXIT"


class CFGGenerator:
    """Parses code lines and constructs block-level Control Flow Graphs."""

    def generate(self, lines: List[str]) -> CFGGraph:
        graph = CFGGraph()
        
        # Initialize entry and exit blocks
        entry = BasicBlock(block_id=graph.entry_block)
        graph.blocks[graph.entry_block] = entry

        current_block = entry
        block_idx = 1

        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue

            current_block.statements.append(stripped)

            # Check branch triggers (conditions/loops)
            if re.search(r'\b(if|while|for|switch|case|return)\b', stripped):
                # Save block and create successor
                next_block_id = f"B_{block_idx}"
                block_idx += 1
                next_block = BasicBlock(block_id=next_block_id)
                graph.blocks[next_block_id] = next_block

                # Connect edges
                current_block.successors.append(next_block_id)
                next_block.predecessors.append(current_block.block_id)
                
                current_block = next_block

        # Link last block to exit block
        exit_block = BasicBlock(block_id=graph.exit_block)
        graph.blocks[graph.exit_block] = exit_block
        current_block.successors.append(graph.exit_block)
        exit_block.predecessors.append(current_block.block_id)

        return graph
