"""Tenant scope — the security-critical part of the routed path.

INVARIANT
---------
Authorization is resolved by the HTTP layer BEFORE the graph starts. The graph
carries the resolved scope as an immutable value. No node, no router output, no
decomposition result and no branch may widen it.

Enforcement is positive, not by convention: `Scope` is frozen, every branch
payload is built from the parent scope by `for_branch()`, and `assert_parity()`
re-checks the branch scope against the parent immediately before retrieval. A
missing or empty scope fails closed (`ScopeError`) rather than searching
everything.
"""
from dataclasses import dataclass


class ScopeError(RuntimeError):
    """Missing, empty or widened scope. Always fail closed — never search wider."""


@dataclass(frozen=True)
class Scope:
    """Resolved, authorized retrieval scope for exactly one request.

    kind:          "single" | "multi" | "group"
    tenant_ids:    the complete allowed set (tuple => cannot be mutated in place)
    single_tenant: the one tenant for kind == "single", else None
    """
    kind: str
    tenant_ids: tuple[str, ...]
    single_tenant: str | None = None

    def __post_init__(self):
        if self.kind not in ("single", "multi", "group"):
            raise ScopeError(f"unknown scope kind: {self.kind!r}")
        if not self.tenant_ids:
            raise ScopeError("empty tenant scope — refusing to retrieve")
        if any((not t) for t in self.tenant_ids):
            raise ScopeError("blank tenant id in scope")
        if self.kind == "single":
            if not self.single_tenant:
                raise ScopeError("single scope without a tenant id")
            if tuple(self.tenant_ids) != (self.single_tenant,):
                raise ScopeError("single scope inconsistent with tenant_ids")
        elif self.single_tenant is not None:
            raise ScopeError(f"{self.kind} scope must not carry single_tenant")

    # ---------------------------------------------------------------- fan-out
    def for_branch(self) -> dict:
        """Scope payload handed to ONE Send branch — the exact parent scope.

        Returned as plain data because LangGraph serialises Send payloads; the
        receiving side rebuilds a Scope and re-verifies parity.
        """
        return {"kind": self.kind,
                "tenant_ids": list(self.tenant_ids),
                "single_tenant": self.single_tenant}

    @staticmethod
    def from_payload(payload: dict) -> "Scope":
        return Scope(kind=payload["kind"],
                     tenant_ids=tuple(payload["tenant_ids"] or ()),
                     single_tenant=payload.get("single_tenant"))

    def assert_parity(self, other: "Scope") -> None:
        """Fail closed unless `other` is exactly this scope. Called per branch."""
        if (other.kind != self.kind
                or set(other.tenant_ids) != set(self.tenant_ids)
                or len(other.tenant_ids) != len(self.tenant_ids)
                or other.single_tenant != self.single_tenant):
            raise ScopeError(
                "branch scope does not match request scope — refusing to retrieve")

    def as_metadata(self) -> dict:
        """Safe trace metadata: the COUNT and kind only, never tenant ids."""
        return {"scope_kind": self.kind, "scope_tenant_count": len(self.tenant_ids)}


def single(tenant_id: str) -> Scope:
    if not (tenant_id or "").strip():
        raise ScopeError("single scope requires a tenant id")
    t = tenant_id.strip()
    return Scope(kind="single", tenant_ids=(t,), single_tenant=t)


def multi(tenant_ids, kind: str = "multi") -> Scope:
    """Dedupe preserving order — matches the existing group endpoint exactly."""
    ordered = tuple(dict.fromkeys([t for t in (tenant_ids or []) if t]))
    return Scope(kind=kind, tenant_ids=ordered, single_tenant=None)
