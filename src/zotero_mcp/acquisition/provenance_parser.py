from __future__ import annotations

_HEADER = "[Paper Ingest Provenance]"
_BOOLEAN_FIELDS = frozenset({"has_fulltext"})


def parse_provenance_from_extra(extra: str | None) -> dict | None:
    if not extra:
        return None

    lines = extra.splitlines()

    header_idx = None
    for i, line in enumerate(lines):
        if line.strip() == _HEADER:
            header_idx = i
            break

    if header_idx is None:
        return None

    result: dict = {}
    for line in lines[header_idx + 1 :]:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            break
        if ": " in stripped:
            key, value = stripped.split(": ", 1)
            key = key.strip()
            value = value.strip()
            if key in _BOOLEAN_FIELDS:
                if value.lower() == "true":
                    result[key] = True
                elif value.lower() == "false":
                    result[key] = False
                else:
                    result[key] = value
            else:
                result[key] = value

    return result


def serialize_provenance_to_extra(provenance: dict) -> str:
    lines = [_HEADER]
    for key, value in provenance.items():
        if isinstance(value, bool):
            lines.append(f"{key}: {'true' if value else 'false'}")
        elif value is not None:
            lines.append(f"{key}: {value}")
    return "\n".join(lines)
