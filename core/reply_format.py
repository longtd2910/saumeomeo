import re


def markdown_to_discord(text: str) -> str:
    if not text:
        return text
    s = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = s.split("\n")
    out = []
    for line in lines:
        stripped = line.lstrip(" ")
        lead = len(line) - len(stripped)

        if re.match(r"^---+\s*$", stripped) or re.match(r"^\*\*\*+\s*$", stripped):
            out.append("")
            continue

        if re.match(r"^#{1,6}\s+", stripped):
            title = re.sub(r"^#{1,6}\s+", "", stripped).strip()
            out.append(f"**{title}**")
            continue

        if re.match(r"^\d+\.\s", stripped):
            normalized = re.sub(r"^(\d+)\.\s+", r"\1. ", stripped)
            if lead:
                out.append(" " * lead + normalized)
            else:
                out.append(normalized)
            continue

        m = re.match(r"^\*\s+(.+)$", stripped)
        if m:
            depth = max(0, lead // 4)
            pad = "    " * depth
            out.append(f"{pad}- {m.group(1).strip()}")
            continue

        out.append(line.rstrip())

    result = "\n".join(out)
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result.strip()
