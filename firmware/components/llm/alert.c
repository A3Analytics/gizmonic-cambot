#include "witness_alert.h"

int witness_alert_edge(int *previous_hit, double score, int when_yes) {
    int yes;
    int hit;
    int send;
    if (!previous_hit) {
        return 0;
    }
    yes = score >= 0.5;
    hit = when_yes ? yes : !yes;
    send = hit && !*previous_hit;
    *previous_hit = hit;
    return send;
}
