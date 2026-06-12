/**
 * bench_entry.c  –  Algorithm Benchmark Entry
 *
 * ============================================================
 *  HOW TO USE (algorithm engineer)
 * ============================================================
 *  1. Set MODULE_NAME to identify this build (e.g. "MyAlgo-v2.0").
 *  2. Include your algorithm header(s) below.
 *  3. Add static wrapper functions (one per test item).
 *  4. Add entries to bench_table[].
 *  5. Build with:  conan build . -pr:h profiles/<target>.profile
 *
 *  Everything else (linker script, descriptor, RAM init, timing)
 *  is handled automatically – do NOT modify below the separator.
 * ============================================================
 */
#include <stdint.h>
#include "core/het_bench_core.h"


/* ============================================================
 * USER ZONE (algorithm engineer edits only this block)
 * ============================================================ */

#define MODULE_NAME "LibBench"

enum {
	BENCH_VECTOR_LEN = 128U,
};

static float g_a[BENCH_VECTOR_LEN];
static float g_b[BENCH_VECTOR_LEN];
static float g_y[BENCH_VECTOR_LEN];

static void bench_prepare_input(void)
{
	uint32_t i;
	for (i = 0U; i < BENCH_VECTOR_LEN; ++i) {
		g_a[i] = (float)i * 0.25f;
		g_b[i] = (float)(BENCH_VECTOR_LEN - i) * 0.5f;
		g_y[i] = 0.0f;
	}
}

static int bench_add_case(const void * const ctx)
{
	(void)ctx;
	for (uint32_t i = 0U; i < BENCH_VECTOR_LEN; ++i) {
                g_y[i] = g_a[i] + g_b[i];
        }
	return g_y[0] == (g_a[0] + g_b[0]);
}

static int bench_sub_case(const void * const ctx)
{
	(void)ctx;
	for (uint32_t i = 0U; i < BENCH_VECTOR_LEN; ++i) {
                g_y[i] = g_a[i] - g_b[i];
        }
	return g_y[0] == (g_a[0] - g_b[0]);
}

static const Case bench_table[] = {
	BENCHMARK_CASE_IMPLEMENTATION("test_add_n128", 0, bench_add_case, 100U),
	BENCHMARK_CASE_IMPLEMENTATION("test_sub_n128", 0, bench_sub_case, 100U),
};


BENCHMARK_IMPLEMENTATION(MODULE_NAME, bench_table);
