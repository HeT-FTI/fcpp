/*
 * Cross-compile probe for .github/workflows/cross-compile.yml.
 *
 * Deliberately dependency-free. The template's own sources pull in
 * zlib/pcre2/eigen (and etl on baremetal), so compiling them here would test
 * the dependency graph rather than the toolchain. This probe answers a single
 * question: does the target toolchain turn source into objects of the expected
 * architecture (Class, Machine, Thumb)?
 */
#include <stdint.h>

volatile uint32_t cross_probe_sink;

uint32_t cross_probe_accumulate(const uint8_t* data, uint32_t len)
{
    uint32_t acc = 0u;
    for (uint32_t i = 0u; i < len; ++i)
    {
        acc = (acc << 1) ^ data[i];
    }
    cross_probe_sink = acc;
    return acc;
}
