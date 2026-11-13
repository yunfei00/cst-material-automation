"""Conservative component hierarchy inferred from CST full Solid IDs.

Only explicit path separators establish parent-child relations. Do not infer
hierarchy from similarly named components.
"""
from __future__ import annotations


def component_hierarchy(catalog: dict[str, str]) -> dict:
    if not isinstance(catalog, dict) or not catalog:
        raise ValueError("Solid 目录为空")
    components: dict[str, dict] = {}
    for solid_id, material in catalog.items():
        if not isinstance(solid_id, str) or ":" not in solid_id:
            raise ValueError(f"无效 Solid ID：{solid_id!r}")
        component, solid = solid_id.rsplit(":", 1)
        if not component or not solid:
            raise ValueError(f"无效 Solid ID：{solid_id!r}")
        # CST component paths may be slash or backslash delimited.
        normalized = component.replace("\\", "/")
        parts = normalized.split("/")
        if not all(parts):
            raise ValueError(f"组件路径无效：{component!r}")
        for depth in range(1, len(parts) + 1):
            key = "/".join(parts[:depth])
            parent = "/".join(parts[:depth - 1]) if depth > 1 else None
            node = components.setdefault(key, {
                "path": key, "parent": parent, "children": [], "solids": [],
            })
            if parent:
                children = components[parent]["children"]
                if key not in children:
                    children.append(key)
        components[normalized]["solids"].append({
            "solid_id": solid_id, "material": material,
        })
    leaves = [key for key, node in components.items()
              if not node["children"] and node["solids"]]
    return {
        "schema_version": 1,
        "source": "inferred_from_solid_ids",
        "components": components,
        "leaf_components": leaves,
        "solid_count": len(catalog),
        "leaf_solid_count": sum(len(components[key]["solids"]) for key in leaves),
    }
