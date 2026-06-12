/**
 * bench_main_linux.c  —  A-core / Linux host port
 *
 * ============================================================
 *  这是 A 核 Linux 构建的唯一新增文件。
 *  它扮演的角色等同于 MCU 侧 Base Firmware 的 bench_host.c：
 *    • 提供 main() 入口
 *    • 实现 HostInterface（计时 + 输出）
 *    • 调用 bench_module_entry()
 *
 *  bench_entry.c / het_bench_core.c 与 MCU 构建完全共用，无需修改。
 *
 *  输出格式与 MCU UART 协议完全一致，可被相同的 CI 解析器处理：
 *    BENCHMARK_START
 *    MODULE|<名称>|cases=N
 *    RESULT|<用例名>|<微秒数>
 *    BENCHMARK_END
 * ============================================================
 */

#include <stdio.h>
#include <stdint.h>
#include <time.h>
#include "core/het_bench_core.h"

/* bench_module_entry 由 bench_entry.c 中的 BENCHMARK_IMPLEMENTATION 宏定义 */
extern int bench_module_entry(const HostInterface *hostApi);

/**
 * 计时：CLOCK_MONOTONIC，单位微秒（µs）。
 * uint32_t 可容纳约 71 分钟；benchmark 用例通常在秒级内完成，不会溢出。
 */
static uint32_t linux_get_ticks(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint32_t)((uint64_t)ts.tv_sec * 1000000ULL
                    + (uint64_t)ts.tv_nsec / 1000ULL);
}

/**
 * 输出：写入 stdout，deploy 脚本（ADB / SSH）直接捕获。
 */
static void linux_write(const char *data, uint32_t len)
{
    fwrite(data, 1, (size_t)len, stdout);
    fflush(stdout);
}

int main(void)
{
    static const HostInterface host = {
        .getTicks = linux_get_ticks,
        .write    = linux_write,
    };
    /* bench_module_entry 返回 1 = 全部通过，0 = 有用例失败 */
    return bench_module_entry(&host) ? 0 : 1;
}
