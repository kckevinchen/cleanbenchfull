"""Validate native output identities and literal replacement strings."""


def validate_result(result, rows, header, locations):
    for field in ("changes", "native_proposals", "detections"):
        seen = set()
        for cell in result.get(field, []):
            i, j = cell["row"], cell["col"]
            if (
                type(i) is not int
                or type(j) is not int
                or not 0 <= i < len(rows)
                or not 0 <= j < len(header)
                or (i, j) in seen
            ):
                raise ValueError("Invalid native coordinates")
            seen.add((i, j))
            if field != "detections" and not isinstance(cell["corrected_value"], str):
                raise ValueError("Non-string native repair")
            if locations is not None and (i, j) not in locations:
                raise ValueError("Native correction proposed a cell outside the supplied mask")
    if result.get("labeled_rows"):
        raise ValueError("Test labels are forbidden")
