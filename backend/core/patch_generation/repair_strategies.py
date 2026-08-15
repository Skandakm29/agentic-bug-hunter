"""
Repair Strategy Guidance Registry  (Phase 3D.1)
================================================
Provides category-specific repair guidance injected into LLM prompts.
Each ``RepairCategory`` maps to a ``RepairGuidance`` object that contains:
  • description    — What this category of bug looks like.
  • common_patterns— Typical code patterns that trigger this bug.
  • repair_approach— Step-by-step fix approach for the LLM to follow.
  • safety_notes   — Critical safety requirements specific to this category.
  • pitfalls       — Common mistakes to avoid when generating the fix.
  • example_fix    — Representative before/after C++ snippet.

Design invariants
-----------------
- All 20 categories from the §3D.1 spec are pre-registered.
- New categories can be added at runtime via ``register()``.
- Missing categories return a generic UNKNOWN guidance rather than raising
  (unless ``strictness`` is enabled in ``PatchGenerationConfig``).
- The registry is a singleton-by-convention (instantiate once and inject).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from backend.core.patch_generation.patch_models import RepairCategory

logger = logging.getLogger("backend.patch_generation.repair_strategies")


# ---------------------------------------------------------------------------
# Guidance data model
# ---------------------------------------------------------------------------


@dataclass
class RepairGuidance:
    """Complete repair guidance for a single bug category."""

    category:         RepairCategory
    description:      str
    common_patterns:  List[str]     = field(default_factory=list)
    repair_approach:  str           = ""
    safety_notes:     str           = ""
    pitfalls:         List[str]     = field(default_factory=list)
    example_fix:      str           = ""


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class RepairGuidanceRegistry:
    """
    Registry mapping every ``RepairCategory`` to its ``RepairGuidance``.

    Usage
    -----
        registry = RepairGuidanceRegistry()
        guidance  = registry.get(RepairCategory.NULL_POINTER_CHECK)
        # guidance.repair_approach, guidance.safety_notes, …
    """

    def __init__(self) -> None:
        self._registry: Dict[RepairCategory, RepairGuidance] = {}
        self._register_all()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self, category: RepairCategory) -> RepairGuidance:
        """
        Return guidance for *category*.  Falls back to UNKNOWN guidance
        if the category has no registered entry.
        """
        guidance = self._registry.get(category)
        if guidance is None:
            logger.warning(
                "RepairGuidanceRegistry: no guidance for category '%s', "
                "falling back to UNKNOWN.",
                category,
            )
            return self._registry[RepairCategory.UNKNOWN]
        return guidance

    def register(self, guidance: RepairGuidance) -> None:
        """Register or overwrite guidance for ``guidance.category``."""
        self._registry[guidance.category] = guidance
        logger.debug(
            "RepairGuidanceRegistry: registered category '%s'.", guidance.category
        )

    def list_categories(self) -> List[RepairCategory]:
        """Return sorted list of all registered categories."""
        return sorted(self._registry.keys(), key=lambda c: c.value)

    def has_guidance(self, category: RepairCategory) -> bool:
        """Return True if *category* has a registered guidance entry."""
        return category in self._registry

    # ------------------------------------------------------------------
    # Internal: bulk registration
    # ------------------------------------------------------------------

    def _register_all(self) -> None:
        """Pre-register guidance for all 20 spec categories + UNKNOWN."""
        entries = [
            # ── NULL POINTER CHECK ───────────────────────────────────────
            RepairGuidance(
                category=RepairCategory.NULL_POINTER_CHECK,
                description="Dereferencing a pointer that may be null causes undefined behaviour.",
                common_patterns=[
                    "ptr->method() without null check",
                    "return value of allocation not checked before use",
                    "function output assigned then used without validation",
                ],
                repair_approach=(
                    "1. Identify every dereference site of the pointer.\n"
                    "2. Add an explicit null check (if (ptr == nullptr)) before each dereference.\n"
                    "3. Decide on a recovery path: return early, throw an exception, or use a default.\n"
                    "4. Do NOT restructure unrelated code."
                ),
                safety_notes=(
                    "Ensure the null-check branch is reachable and handles cleanup of any "
                    "resources acquired before the check.  Do not add unnecessary logging "
                    "in interrupt contexts."
                ),
                pitfalls=[
                    "Adding the check after the dereference (too late)",
                    "Forgetting to handle the null case (silent no-op)",
                    "Introducing double-check patterns in multi-threaded code without a mutex",
                ],
                example_fix=(
                    "// BEFORE\n"
                    "ptr->doWork();\n\n"
                    "// AFTER\n"
                    "if (ptr != nullptr) {\n"
                    "    ptr->doWork();\n"
                    "} else {\n"
                    "    return ErrorCode::NULL_POINTER;\n"
                    "}"
                ),
            ),
            # ── MEMORY LEAK ─────────────────────────────────────────────
            RepairGuidance(
                category=RepairCategory.MEMORY_LEAK,
                description="Heap memory allocated but never freed on all code paths.",
                common_patterns=[
                    "new without delete",
                    "malloc without free",
                    "early return skipping cleanup",
                    "exception unwinds past cleanup code",
                ],
                repair_approach=(
                    "1. Identify the allocation site.\n"
                    "2. Identify ALL exit paths from the enclosing scope.\n"
                    "3. Prefer converting raw owning pointers to std::unique_ptr.\n"
                    "4. If raw pointers must be kept, add delete on every exit path.\n"
                    "5. Validate that exception paths are covered."
                ),
                safety_notes=(
                    "Prefer RAII (unique_ptr, shared_ptr) over manual delete.  "
                    "Never delete a stack-allocated object."
                ),
                pitfalls=[
                    "Using delete instead of delete[] for array allocations",
                    "Deleting a pointer that may also be used after the call",
                    "Double-free when exception-path delete was already added",
                ],
                example_fix=(
                    "// BEFORE\n"
                    "int* buf = new int[n];\n"
                    "if (error) return -1;  // leak\n"
                    "delete[] buf;\n\n"
                    "// AFTER\n"
                    "auto buf = std::make_unique<int[]>(n);\n"
                    "if (error) return -1;  // unique_ptr destructs automatically\n"
                ),
            ),
            # ── DANGLING POINTER ─────────────────────────────────────────
            RepairGuidance(
                category=RepairCategory.DANGLING_POINTER,
                description="Pointer used after the memory it points to has been freed.",
                common_patterns=[
                    "delete ptr; ptr->method();",
                    "returning address of local variable",
                    "iterator invalidated then dereferenced",
                ],
                repair_approach=(
                    "1. Identify the deallocation site.\n"
                    "2. Identify every use of the pointer after deallocation.\n"
                    "3. Either null the pointer immediately after delete, or restructure "
                    "   so the use precedes the delete.\n"
                    "4. Consider replacing with weak_ptr to detect dangling ownership."
                ),
                safety_notes="Set pointers to nullptr after delete to detect stale usage early.",
                pitfalls=["Forgetting copies of the pointer that also dangle"],
            ),
            # ── BUFFER OVERFLOW ──────────────────────────────────────────
            RepairGuidance(
                category=RepairCategory.BUFFER_OVERFLOW,
                description="Writing or reading beyond the allocated bounds of an array or buffer.",
                common_patterns=[
                    "Fixed-size array indexed with unchecked variable",
                    "strcpy / sprintf with insufficient destination size",
                    "Off-by-one in loop bounds",
                ],
                repair_approach=(
                    "1. Identify the buffer and its size.\n"
                    "2. Identify the access expression.\n"
                    "3. Add a bounds check before the access.\n"
                    "4. Prefer std::vector or std::span with at() for automatic bounds checking.\n"
                    "5. Replace unsafe C string functions with safe alternatives (strncpy, snprintf)."
                ),
                safety_notes=(
                    "Never use sizeof(ptr) for a pointer; use the known array size or a "
                    "runtime-tracked length variable."
                ),
                pitfalls=[
                    "Off-by-one: using < instead of <= or vice-versa",
                    "Mixing byte lengths with element counts",
                ],
            ),
            # ── API MISUSE ───────────────────────────────────────────────
            RepairGuidance(
                category=RepairCategory.API_MISUSE,
                description="Incorrect use of a library or system API (wrong parameters, wrong order, missing preconditions).",
                common_patterns=[
                    "Calling an API before required initialisation",
                    "Passing wrong argument types or out-of-range values",
                    "Ignoring mandatory return-value checks",
                ],
                repair_approach=(
                    "1. Consult the API contract/documentation embedded in context.\n"
                    "2. Fix the call to match the documented signature and preconditions.\n"
                    "3. Add error-return checking if the API may fail.\n"
                    "4. Do not change the API; fix the caller."
                ),
                safety_notes="Never cast away const to satisfy an API; fix the API usage instead.",
                pitfalls=["Fixing one call site but missing other call sites of the same API"],
            ),
            # ── RESOURCE LEAK ────────────────────────────────────────────
            RepairGuidance(
                category=RepairCategory.RESOURCE_LEAK,
                description="File handle, socket, mutex, or other OS resource not released on all paths.",
                common_patterns=[
                    "fopen without fclose on all paths",
                    "open()/close() imbalance",
                    "Mutex lock without corresponding unlock",
                ],
                repair_approach=(
                    "1. Identify the resource acquisition site.\n"
                    "2. Identify all exit paths (including exception paths).\n"
                    "3. Wrap the resource in an RAII guard (std::lock_guard, FILE* in unique_ptr with custom deleter).\n"
                    "4. Validate all paths release the resource."
                ),
                safety_notes="Use RAII in preference to manual acquire/release pairs.",
                pitfalls=["Exception path skipping manual release"],
            ),
            # ── INCORRECT CONDITION ──────────────────────────────────────
            RepairGuidance(
                category=RepairCategory.INCORRECT_CONDITION,
                description="A Boolean condition evaluates incorrectly, causing wrong branch selection.",
                common_patterns=[
                    "== vs = in condition (assignment in condition)",
                    "Wrong logical operator (&& vs ||)",
                    "Negation applied to wrong sub-expression",
                    "Signed/unsigned comparison always true/false",
                ],
                repair_approach=(
                    "1. State the intended invariant for the condition.\n"
                    "2. Rewrite the condition to match the invariant exactly.\n"
                    "3. Add a comment explaining the intended logic.\n"
                    "4. Do not change surrounding code."
                ),
                safety_notes="Avoid side effects inside conditions (e.g. func() in condition).",
                pitfalls=["Inverting an entire compound condition when only one operand is wrong"],
            ),
            # ── INCORRECT LOOP ───────────────────────────────────────────
            RepairGuidance(
                category=RepairCategory.INCORRECT_LOOP,
                description="Loop iterates too many or too few times, or has incorrect termination.",
                common_patterns=[
                    "Off-by-one in loop bound",
                    "Iterator not advanced (infinite loop)",
                    "Wrong stride",
                ],
                repair_approach=(
                    "1. Determine the correct iteration range.\n"
                    "2. Fix the initialisation, condition, and/or increment.\n"
                    "3. Do not refactor the loop body."
                ),
                safety_notes="Verify that the loop variable does not overflow its type.",
                pitfalls=["Changing loop body instead of loop bounds"],
            ),
            # ── MISSING RETURN ───────────────────────────────────────────
            RepairGuidance(
                category=RepairCategory.MISSING_RETURN,
                description="A non-void function may fall off the end without returning a value.",
                common_patterns=[
                    "if/else chain with missing else branch",
                    "switch without default case",
                    "Early return on error path but no return on success path",
                ],
                repair_approach=(
                    "1. Identify the execution path that has no return statement.\n"
                    "2. Add an appropriate return value at the end of that path.\n"
                    "3. If no sensible value exists, consider throwing an exception or asserting."
                ),
                safety_notes="Do not return a local reference or address of a local variable.",
                pitfalls=["Returning an uninitialised variable to satisfy the compiler"],
            ),
            # ── EXCEPTION HANDLING ───────────────────────────────────────
            RepairGuidance(
                category=RepairCategory.EXCEPTION_HANDLING,
                description="Exceptions not caught, caught too broadly, or thrown in destructors.",
                common_patterns=[
                    "catch(...) swallowing all exceptions",
                    "Exception thrown from destructor",
                    "Missing try/catch around throwing API calls",
                ],
                repair_approach=(
                    "1. Identify the specific exception types that can be thrown.\n"
                    "2. Add targeted catch clauses in the correct order (specific before general).\n"
                    "3. Handle or re-throw; never silently swallow.\n"
                    "4. Mark destructors noexcept and wrap risky operations."
                ),
                safety_notes="Never let exceptions escape from destructors; wrap in try/catch and log.",
                pitfalls=["Using catch(std::exception&) when catch(std::bad_alloc&) is needed first"],
            ),
            # ── BOUNDARY CONDITION ───────────────────────────────────────
            RepairGuidance(
                category=RepairCategory.BOUNDARY_CONDITION,
                description="Edge cases at the boundaries of value ranges not handled correctly.",
                common_patterns=[
                    "Empty container not checked before first-element access",
                    "Zero value causing division by zero",
                    "INT_MAX/INT_MIN causing overflow on arithmetic",
                ],
                repair_approach=(
                    "1. Identify the boundary value(s).\n"
                    "2. Add an explicit guard for the boundary case.\n"
                    "3. Define the expected behaviour at the boundary and document it."
                ),
                safety_notes="Prefer early return over nested ifs for boundary guards.",
                pitfalls=["Guarding one boundary but missing the symmetric one"],
            ),
            # ── UNINITIALIZED VARIABLE ───────────────────────────────────
            RepairGuidance(
                category=RepairCategory.UNINITIALIZED_VARIABLE,
                description="Variable used before being assigned a value.",
                common_patterns=[
                    "int x; … use(x); without assignment",
                    "Struct member not initialised in constructor",
                    "Variable initialised conditionally, used unconditionally",
                ],
                repair_approach=(
                    "1. Identify the variable and its first use.\n"
                    "2. Add an initialiser at the declaration site.\n"
                    "3. Choose a safe default value consistent with the function's invariants."
                ),
                safety_notes="Do not initialise to a sentinel that masks real bugs (e.g. 0 for a size).",
                pitfalls=["Initialising to a value that hides the bug rather than fixing it"],
            ),
            # ── USE AFTER FREE ───────────────────────────────────────────
            RepairGuidance(
                category=RepairCategory.USE_AFTER_FREE,
                description="Heap object accessed after it has been deallocated.",
                common_patterns=[
                    "Caching raw pointer to an object then freeing the owner",
                    "shared_ptr expired but raw pointer cached elsewhere",
                ],
                repair_approach=(
                    "1. Identify the deallocation point.\n"
                    "2. Identify all uses of the freed memory after that point.\n"
                    "3. Restructure so the use precedes the free, or extend the lifetime.\n"
                    "4. Consider replacing with std::weak_ptr for optional access."
                ),
                safety_notes="Always null raw pointers after delete to catch use-after-free at runtime.",
                pitfalls=["Only nulling one copy of a multiply-aliased pointer"],
            ),
            # ── INCORRECT LIFETIME ───────────────────────────────────────
            RepairGuidance(
                category=RepairCategory.INCORRECT_LIFETIME,
                description="Object lifetime does not match its intended usage period.",
                common_patterns=[
                    "Temporary object bound to reference outliving the expression",
                    "Stack object address returned from function",
                    "Static local with unexpected initialisation order",
                ],
                repair_approach=(
                    "1. Identify the object whose lifetime is wrong.\n"
                    "2. Extend the lifetime (move to outer scope, allocate on heap, make static).\n"
                    "3. Update ownership semantics accordingly."
                ),
                safety_notes="Returning references to locals is undefined behaviour; never do it.",
                pitfalls=["Extending lifetime by making something static when thread safety is needed"],
            ),
            # ── MISSING CLEANUP ──────────────────────────────────────────
            RepairGuidance(
                category=RepairCategory.MISSING_CLEANUP,
                description="Resources or state not properly cleaned up on function exit.",
                common_patterns=[
                    "Mutex not unlocked on error return",
                    "Temporary file not deleted on exception",
                    "Global state not restored",
                ],
                repair_approach=(
                    "1. Identify the state that must be cleaned up.\n"
                    "2. Add cleanup to all missing exit paths.\n"
                    "3. Prefer RAII guards over manual cleanup."
                ),
                safety_notes="Use ScopeExit / defer patterns for complex cleanup sequences.",
                pitfalls=["Only adding cleanup to the happy path but not error paths"],
            ),
            # ── INCORRECT OWNERSHIP ──────────────────────────────────────
            RepairGuidance(
                category=RepairCategory.INCORRECT_OWNERSHIP,
                description="Ownership of a resource is ambiguous or transferred incorrectly.",
                common_patterns=[
                    "Raw owning pointer passed to function that does not delete it",
                    "unique_ptr passed by value (ownership transferred) unintentionally",
                    "Double-free due to multiple owners",
                ],
                repair_approach=(
                    "1. Determine the intended owner of the resource.\n"
                    "2. Use unique_ptr for single-owner, shared_ptr for shared-owner.\n"
                    "3. Pass by reference when ownership is not transferred.\n"
                    "4. Document ownership in comments."
                ),
                safety_notes="Prefer explicit ownership types over raw pointers with implicit contracts.",
                pitfalls=["Using shared_ptr when unique_ptr suffices, causing unnecessary overhead"],
            ),
            # ── STL MISUSE ───────────────────────────────────────────────
            RepairGuidance(
                category=RepairCategory.STL_MISUSE,
                description="Standard library containers or algorithms used incorrectly.",
                common_patterns=[
                    "Iterator invalidation after container modification",
                    "Calling front()/back() on empty container",
                    "Using std::map[] creating default-inserted elements unintentionally",
                ],
                repair_approach=(
                    "1. Identify the misused STL operation.\n"
                    "2. Replace with the correct equivalent or add the required precondition check.\n"
                    "3. Prefer range-for and STL algorithms over raw index loops."
                ),
                safety_notes="Check iterator validity after any container-modifying operation.",
                pitfalls=["Calling erase() inside a range-for loop (iterator invalidation)"],
            ),
            # ── CONST CORRECTNESS ────────────────────────────────────────
            RepairGuidance(
                category=RepairCategory.CONST_CORRECTNESS,
                description="Missing or incorrect use of const, allowing unintended mutation.",
                common_patterns=[
                    "Member function modifying state but not declared const",
                    "Const reference passed to non-const parameter",
                    "const_cast removing const for mutation",
                ],
                repair_approach=(
                    "1. Add const to the correct declaration site.\n"
                    "2. Propagate const through callers if necessary.\n"
                    "3. Never use const_cast to remove const for mutation."
                ),
                safety_notes="const_cast is only safe when the original object is non-const.",
                pitfalls=["Making a function const but forgetting to update its declaration in the header"],
            ),
            # ── UNSAFE CAST ──────────────────────────────────────────────
            RepairGuidance(
                category=RepairCategory.UNSAFE_CAST,
                description="C-style or reinterpret_cast used in a potentially unsafe manner.",
                common_patterns=[
                    "(int*) cast of void* without size validation",
                    "reinterpret_cast violating strict aliasing",
                    "static_cast down hierarchy without dynamic check",
                ],
                repair_approach=(
                    "1. Replace C-style cast with the appropriate C++ cast.\n"
                    "2. Use dynamic_cast for downcasting in polymorphic hierarchies.\n"
                    "3. Use static_cast for well-typed numeric conversions.\n"
                    "4. Avoid reinterpret_cast except for platform-specific ABI code."
                ),
                safety_notes="reinterpret_cast across unrelated pointer types violates strict aliasing.",
                pitfalls=["Using static_cast for a downcast that may fail at runtime"],
            ),
            # ── THREAD SAFETY ────────────────────────────────────────────
            RepairGuidance(
                category=RepairCategory.THREAD_SAFETY,
                description="Shared mutable state accessed from multiple threads without synchronisation.",
                common_patterns=[
                    "Global or static variable written in one thread, read in another",
                    "Double-checked locking implemented incorrectly",
                    "Lock not held for the full duration of a compound operation",
                ],
                repair_approach=(
                    "1. Identify the shared mutable state.\n"
                    "2. Protect all accesses (reads AND writes) with the same mutex.\n"
                    "3. Use std::lock_guard or std::unique_lock for RAII locking.\n"
                    "4. Consider std::atomic for simple scalar types.\n"
                    "5. Do not hold a lock longer than necessary."
                ),
                safety_notes=(
                    "std::atomic is not a replacement for mutexes on compound operations.  "
                    "Be careful of lock-order deadlocks when acquiring multiple mutexes."
                ),
                pitfalls=[
                    "Protecting write but not read (or vice versa)",
                    "Calling external functions while holding a lock (potential deadlock)",
                ],
            ),
            # ── UNKNOWN / FALLBACK ───────────────────────────────────────
            RepairGuidance(
                category=RepairCategory.UNKNOWN,
                description="Generic repair guidance for unclassified bug categories.",
                common_patterns=["Varies by bug type."],
                repair_approach=(
                    "1. Carefully read the bug description and root cause.\n"
                    "2. Generate the minimal change that resolves the stated issue.\n"
                    "3. Preserve all surrounding code unchanged.\n"
                    "4. Add a brief comment explaining the fix."
                ),
                safety_notes="When uncertain, prefer the more defensive approach.",
                pitfalls=["Making unrelated changes while fixing the reported issue"],
            ),
        ]

        for entry in entries:
            self._registry[entry.category] = entry

        logger.debug(
            "RepairGuidanceRegistry: registered %d categories.", len(self._registry)
        )
