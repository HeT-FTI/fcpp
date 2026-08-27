FROM mcr.microsoft.com/devcontainers/base:ubuntu-24.04

# clang-format >= 19 is required for the `Language: C` config section;
# apt ships v18 on ubuntu-24.04, so pin the pip build instead.
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ cmake ninja-build clang clang-tidy \
    python3 python3-pip python3-venv nodejs npm \
    doxygen graphviz lcov jq \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir --break-system-packages conan clang-format==23.1.0 \
    && conan profile detect --force

# Run as the non-root devcontainer user (avoid default-root image finding).
USER vscode
