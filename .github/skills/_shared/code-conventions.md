# fcpp C/C++ Code Conventions（模板私有代码规范）

> Fact source: module generation in `conanfile.py`, `.github/misc/.clang-format`, `.github/misc/.clang-tidy`, `Codegen-Starter.txt`.

## File Organization（文件组织）

1. **Pairing（配对约定）**: every `.h` ↔ `.c`, `.hpp` ↔ `.cpp` one-to-one. 一一对应。
2. **Suffix roles（后缀分工）**: `.h/.c` = C, `.hpp/.cpp` = C++, `.dox` = pure docs, `.cxx` = examples. 后缀分工。
3. Public headers in `include/`, implementations in `src/`, Python bindings in `api/python_bindings.cpp`.

## Module Annotations（模块生成注解）

In Doxygen comments of `include/` + `src/`:
- **`@exporter`**: export this symbol into the generated C++ module. 导出符号。
- **`@attacher`**: attach to the module (not forced export). 附加符号。
```cpp
/**
 * @brief vector add
 * @exporter
 */
void fcpp_vec_add_f32(const float*, const float*, float*, uint32_t);
```

## Module Boundary Markers（模块边界标记）

Wrap the `#include` area with `// Conan::ImportStart` / `// Conan::ImportEnd`; these are converted to `import` when generating modules. 包裹 include 区，生成模块时转为 import。
```cpp
// Conan::ImportStart
#pragma once
#include <cstdint>
// Conan::ImportEnd
```

## Layout Rules（排版规则）

1. **2 blank lines between global objects** (module generation splits on this). 全局对象间强制 2 空行。
2. Multi-line Doxygen `/** ... */` before functions/classes/variables. 声明前用多行 Doxygen。
3. C headers are auto-wrapped with `extern "C"` by the recipe — do not hand-write. C 头自动包 extern "C"。

## Comment Language Spec（注释语言规范）

- Bilingual tags: `@brief [en] English` / `@brief [zh] 中文`. 双语标签。
- Version tag: `@since <version>` → filtered by docs/build.py per version. 版本标注。
- Active languages come from `doc_languages` in metadata. 生效语言由 metadata 决定。

## Format / Static Checks（格式化/静态检查）

- `.clang-format`: C pointer right-aligned, C++ left-aligned; C11/Cpp17; 120 cols; no include sort; no comment reflow. 格式规则。
- `.clang-tidy`: `bugprone-*` + `performance-*` + `cppcoreguidelines-avoid-magic-numbers`; **WarningsAsErrors: '*'**. warning 即失败。
- Local check（本地自查）:
  ```bash
  clang-format --dry-run --Werror include/ src/
  clang-tidy src/*.cpp -- -std=c++17
  ```

## Baremetal Constraints（裸机约束）

- Target-side (main package) deps filtered by `baremetal_white_list`（默认 etl/ArduinoJson）.
- OS-dependent libs (zlib/pcre2/eigen/gtest/pybind11) filtered in baremetal cross-build. 依赖 OS 的库被过滤。
- Guard desktop-only code with `#ifndef __ARM_EABI__` (see `cpptest.cpp`). 桌面专属代码加保护。

