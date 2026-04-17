"""RBAC domain contracts.

Defines roles, permissions, and a permission-check function.
Roles are hierarchical: admin > tenant_admin > project_admin > user > viewer.
"""

from __future__ import annotations

from enum import IntEnum


class Role(IntEnum):
    """Roles ordered by privilege level (higher = more access)."""

    VIEWER = 0
    USER = 1
    PROJECT_ADMIN = 2
    TENANT_ADMIN = 3
    ADMIN = 4


class Permission(str):
    """Named permissions for admin operations."""

    pass


# Permission constants
PERM_ADMIN_READ = Permission("admin:read")
PERM_ADMIN_WRITE = Permission("admin:write")
PERM_TENANT_MANAGE = Permission("tenant:manage")
PERM_PROJECT_MANAGE = Permission("project:manage")
PERM_KEY_MANAGE = Permission("key:manage")
PERM_POLICY_MANAGE = Permission("policy:manage")
PERM_USAGE_READ = Permission("usage:read")

# Role → permissions mapping
_ROLE_PERMISSIONS: dict[Role, set[Permission]] = {
    Role.VIEWER: {PERM_USAGE_READ},
    Role.USER: {PERM_USAGE_READ},
    Role.PROJECT_ADMIN: {PERM_USAGE_READ, PERM_KEY_MANAGE, PERM_PROJECT_MANAGE, PERM_ADMIN_READ},
    Role.TENANT_ADMIN: {
        PERM_USAGE_READ, PERM_KEY_MANAGE, PERM_PROJECT_MANAGE,
        PERM_TENANT_MANAGE, PERM_ADMIN_READ, PERM_ADMIN_WRITE,
    },
    Role.ADMIN: {
        PERM_USAGE_READ, PERM_KEY_MANAGE, PERM_PROJECT_MANAGE,
        PERM_TENANT_MANAGE, PERM_POLICY_MANAGE,
        PERM_ADMIN_READ, PERM_ADMIN_WRITE,
    },
}


def has_permission(role: Role, permission: Permission) -> bool:
    """Check if a role has a specific permission."""
    perms = _ROLE_PERMISSIONS.get(role, set())
    return permission in perms


def has_role_level(role: Role, minimum: Role) -> bool:
    """Check if a role meets a minimum privilege level."""
    return role >= minimum
