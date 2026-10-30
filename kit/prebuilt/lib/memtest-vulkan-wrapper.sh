#!/bin/sh
# Template installed by instance-bootstrap.sh when the host has no vulkan
# loader (hiveOS rigs): @KIT_LIB@ is substituted with the absolute path to
# kit/prebuilt/lib at install time. The real binary sits next to this
# wrapper as memtest_vulkan.bin.
DIR=$(cd "$(dirname "$0")" && pwd)
export LD_LIBRARY_PATH="@KIT_LIB@:${LD_LIBRARY_PATH:-}"
if [ -z "${VK_ICD_FILENAMES:-}" ] && ! ls /usr/share/vulkan/icd.d/*.json /etc/vulkan/icd.d/*.json >/dev/null 2>&1; then
  export VK_ICD_FILENAMES="@KIT_LIB@/nvidia_icd.json"
fi
exec "$DIR/memtest_vulkan.bin" "$@"
