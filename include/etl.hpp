// Conan::ImportStart
#pragma once
#include <cstdint>
#include <etl/algorithm.h>
#include <etl/functional.h>
// Conan::ImportEnd

/**
 * @brief Element-wise addition of two float arrays.
 *        Implemented internally with etl::transform + etl::plus.
 * @param a   Input array A
 * @param b   Input array B
 * @param y   Output array Y = A + B
 * @param n   Number of elements
 */
inline void fcpp_vec_add_f32(const float* a, const float* b, float* y, uint32_t n) {
    etl::transform(a, a + n, b, y, etl::plus<float>());
}

/**
 * @brief Element-wise subtraction of two float arrays.
 *        Implemented internally with etl::transform + etl::minus.
 * @param a   Input array A
 * @param b   Input array B
 * @param y   Output array Y = A - B
 * @param n   Number of elements
 */
inline void fcpp_vec_sub_f32(const float* a, const float* b, float* y, uint32_t n) {
    etl::transform(a, a + n, b, y, etl::minus<float>());
}
