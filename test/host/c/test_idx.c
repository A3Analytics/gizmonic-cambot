#include "witness_idx.h"

#include <stdio.h>
#include <string.h>

int main(void) {
    uint8_t encoded[32];
    const uint8_t expect[] = {0xe8, 0x03, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x02, 0x00, 0x00, 0x00,
                              0x03, 0x00, 0x00, 0x00, 0x04, 0x00};
    int length = witness_idx_encode(encoded, sizeof encoded, 1000, 2, 3, 4);
    if (length != WITNESS_IDX_SIZE) {
        fprintf(stderr, "size %d\n", length);
        return 1;
    }
    if (memcmp(encoded, expect, sizeof expect) != 0) {
        fprintf(stderr, "bytes differ\n");
        return 1;
    }
    return 0;
}
