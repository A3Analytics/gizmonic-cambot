"""Read width and height from a JPEG SOF marker."""


def jpeg_size(jpeg: bytes) -> tuple[int, int]:
    index = 2
    limit = len(jpeg) - 8
    while index < limit:
        if jpeg[index] != 0xFF:
            break
        marker = jpeg[index + 1]
        if marker in (0xC0, 0xC1, 0xC2):
            height = int.from_bytes(jpeg[index + 5 : index + 7], "big")
            width = int.from_bytes(jpeg[index + 7 : index + 9], "big")
            return width, height
        if marker in (0xD8, 0xD9):
            index += 2
            continue
        if index + 4 > len(jpeg):
            break
        length = int.from_bytes(jpeg[index + 2 : index + 4], "big")
        if length < 2:
            break
        index += 2 + length
    return 0, 0
