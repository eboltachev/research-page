from __future__ import annotations

from collections.abc import Mapping


def prometheus_text(metrics: Mapping[str, int | float]) -> str:
    lines: list[str] = []
    for name, value in sorted(metrics.items()):
        metric = name.lower()
        lines.append(f"# TYPE {metric} counter")
        lines.append(f"{metric} {value}")
    return "\n".join(lines) + "\n"
