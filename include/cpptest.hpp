// Conan::ImportStart
#pragma once
#include <array>
#include <iostream>
#include <string>
#include <tuple>
#include <utility>
#include <vector>
#ifndef __ARM_EABI__
#include <zlib.h>
#include <Eigen/Dense>
#endif
// Conan::ImportEnd



/**
 * @brief test function in cpp
 * @exporter
 */
inline void test_hello() { std::cout << "CPP Compiler is ready!" << '\n'; };



#ifndef __ARM_EABI__
/**
 * @brief test eigen in cpp
 * @exporter
 */
inline void test_eigen() {
    Eigen::Matrix3d A;
    A << 1, 2, 3, 4, 5, 6, 7, 8, 9;
    std::cout << "matrix A:\n" << A << "; Eigen Matrix test done!" << '\n';
}



/**
 * @brief zlib requirement test in CPP compiler
 * @exporter
 */
inline void test_cpp_zlib() {
    const char in[] = "Hello, zlib in CPP!";
    std::array<unsigned char, 128> out{};
    std::array<unsigned char, 128> rec{};
    auto len_out = static_cast<uLong>(out.size());
    auto len_rec = static_cast<uLong>(rec.size());
    if (auto compress_result =
            compress(out.data(), &len_out, reinterpret_cast<const Bytef*>(in), static_cast<uLong>(sizeof(in)));
        compress_result != Z_OK) {
        std::cerr << "Compression failed with error code: " << compress_result << '\n';
        return;
    }
    if (auto uncompress_result = uncompress(rec.data(), &len_rec, reinterpret_cast<const Bytef*>(out.data()), len_out);
        uncompress_result != Z_OK) {
        std::cerr << "Decompression failed with error code: " << uncompress_result << '\n';
        return;
    }
    std::cout << "Original: " << in << "; Decompressed: " << reinterpret_cast<char*>(rec.data())
              << "; zlib in C++ test done!" << '\n';
}
#endif /* __ARM_EABI__ */



/**
 * @brief test class declaration
 * @exporter
 */
class Person {
  public:
    Person(std::string n, int a);
    std::string greet() const;
    std::string name;
    int age;
};



/**
 * @brief test Person construction
 * @param n name
 * @param a age
 * @attacher
 */
inline Person::Person(std::string n, int a) : name(std::move(n)), age(a) {};



/**
 * @brief test Person meth 2
 * @return the hello message
 * @attacher
 */
inline std::string Person::greet() const { return "Hello, I'm " + name; };



/**
 * @brief test template func in headers
 * @tparam T vector like
 * @param vec iterable thing of numbers
 * @return a numeric
 * @exporter
 */
template <typename T> auto test_sum(const std::vector<T>& vec) {
    T sum = T();
    for (const T& elem : vec) {
        sum += elem;
    }
    return sum;
};



/**
 * @brief simple RGB color template
 * @tparam T type trait
 * @exporter
 */
template <typename T> class Color {
  public:
    Color() = default;
    Color(T red_, T green_, T blue_) : r(red_), g(green_), b(blue_) {};
    void set(T red_, T green_, T blue_) {
        this->r = red_;
        this->g = green_;
        this->b = blue_;
    };
    void print() const { std::cout << "RGB(" << r << ", " << g << ", " << b << ")\n"; };
    auto components() const { return std::make_tuple(r, g, b); };

  private:
    T r{};
    T g{};
    T b{};
};



/**
 * @brief simple RGB int color template
 * @exporter
 */
template class Color<int>;