"""Read committed feature/score vectors shared by the hardware validators."""
from pathlib import Path

FEATURE_NUMBER = 16
VECTOR_BYTES = FEATURE_NUMBER + 4


def load_vectors(
    vector_path: Path,
    count: int,
) -> list[tuple[bytes, int]]:
    vectors: list[tuple[bytes, int]] = []

    for line_number, line in enumerate(
        vector_path.read_text(
            encoding="utf-8"
        ).splitlines(),
        start=1,
    ):
        line = line.strip()

        if not line:
            continue

        try:
            vector = bytes.fromhex(line)
        except ValueError as error:
            raise ValueError(
                f"Invalid hexadecimal vector on line "
                f"{line_number}"
            ) from error

        if len(vector) != VECTOR_BYTES:
            raise ValueError(
                f"Vector line {line_number} contains "
                f"{len(vector)} bytes; expected {VECTOR_BYTES}"
            )

        features = vector[:FEATURE_NUMBER]
        expected_score = int.from_bytes(
            vector[FEATURE_NUMBER:],
            byteorder="big",
            signed=True,
        )

        vectors.append((features, expected_score))

        if len(vectors) == count:
            break

    if len(vectors) != count:
        raise ValueError(
            f"Requested {count} vectors, but "
            f"{vector_path} contains only {len(vectors)}"
        )

    return vectors


