#pragma once

/* previous_hit is 0 or 1 and is updated to the current hit.
 * when_yes selects the alert side: 1 fires while score >= 0.5, 0 fires while score < 0.5.
 * Returns 1 only on the edge into that side. */

int witness_alert_edge(int *previous_hit, double score, int when_yes);
