# Android 17 / mondrian — Qualcomm Vibrator AIDL and offload safety review

**Scope:** Independent PixelExperience-Reborn Android 17 AOSP-first for POCO F5 Pro / Redmi K60 (mondrian, Snapdragon 8+ Gen 1 SM8475). No LineageOS or other ROM base is imported.

## Exact source baseline and API contracts

- Archived PE `seventeen` and `fourteen`: commit `b7bcccdda4941f3b269b368f8ff0fdd02cf181a1` (2024-01-04), Git tree `ecb8c7ace059a36e5c35af755355a651d64d9929`. 18 entries include `aidl/` and `effect/`.
- This repository **already implements Vibrator AIDL V2**, not HIDL. `aidl/vendor.qti.hardware.vibrator.service.xml` declares `android.hardware.vibrator`, version **2**, `IVibrator/default`. `aidl/Android.bp` links `android.hardware.vibrator-V2-ndk` only through a legacy product/Soong `vibratortargets` selection for old Android releases. It does **not** select that build config for Android 17.
- The official pinned Android17 `hardware/interfaces` owner is SHA `735e364cbdd00b0d6e4dc33ea0866dbb6d971a11`. Its frozen `vibrator/aidl/Android.bp` lists AIDL **V1, V2, V3 and V4**. The higher version exposes newer haptic generator/session and other APIs, but it is **not** automatically supported by mondrian's physical vibrator. A lower frozen version may still be valid if explicitly compatible with the device's FCM/VINTF and required features.
- The old `InputFFDevice` scans Linux EV_FF input nodes (`qcom-hv-haptics`, `qti-haptics`, `awinic_haptic`, AW86xx devices) and makes SoC-dependent assumptions about external control. It must not be assumed to describe the actual Xiaomi mondrian haptics driver, vendor effect indices, gain or amplitude map.

## Real verified safety issues in archived source

1. `effect/VibratorPatterns.cpp` allocates a pattern buffer with `new uint8_t[]` but `free_pattern_mem` releases with scalar `delete` — undefined behavior / allocator mismatch. Patch uses `delete[]`, with `new (std::nothrow)` for an explicit `-ENOMEM` result, and makes `VibratorPatterns.h` independently declare its `uint*_t` types via `<stdint.h>`.
2. `OffloadGlinkConnection` had an uninitialized fd, incorrectly retried with negative `-ETIMEDOUT == errno`, returned a **positive** errno from `GlinkWrite` (so `sendData` wrongly treated failures as success), incremented unsigned `size_t` with negative read results, and could spin forever when `read()` returned EOF/zero. Patch fixes those failure paths, `EINTR`, partial I/O, invalid buffers, negative errno, timeout, EOF and descriptor initialization/close.
3. The slate SSR uevent listener kept a `msg_ptr` from a **previous message** and could step beyond the next payload. The patch resets the cursor for each received packet and closes the uevent socket when the listener terminates.
4. `PatternOffload::SendPatterns` had early returns that leaked the Glink channel. The patch guarantees release after configuration/pattern send, and frees the allocated pattern buffer on failure or success. Configuration data remains owned by static storage and is not freed.
5. `vibrator-vendor-product.mk` automatically installs historical `vendor.qti.hardware.vibrator.service` and globally installs `excluded-input-devices.xml`. The isolated PE review branch now bypasses this old vendor recipe for `TARGET_DEVICE=mondrian` until correct Android17 Soong/AIDL version, Xiaomi EV_FF drivers, actual haptic effect mapping and one `IVibrator/default` provider are validated. Behavior for other historical devices is kept.

These are real source fixes. They do not imply that AON/Slate haptic offload exists on mondrian; it is conditional on `ro.vendor.qc_aon_presence`. No new driver or capability is invented.

## Regression coverage

- GitHub Actions runs `tests/check_android17_vibrator_offload.py`, which **compiles the checked-in Glink methods** in a real C++17 POSIX/socketpair harness, tests success and negative-errno/EOF branches, and checks that sends cannot succeed on a broken channel.
- The real `effect/VibratorPatterns.cpp` is compiled into a C++17 executable with **AddressSanitizer + UndefinedBehaviorSanitizer**, repeatedly allocates/releases pattern buffers and validates the public C++ header dependency. This catches the old `new[]`/`delete` allocator mismatch. Host tests don't invoke device drivers.
- GNU Make tests actually include the archived product rule for mondrian/non-mondrian, verify archived service and global input exclusion aren't installed for mondrian and are unchanged for historical targets.
- VINTF is parsed and checked as **Vibrator AIDL V2**, not relabeled as V4. The manifest regression gate separately ensures the archived source does not silently enter the independent AOSP17 project graph.

## Conditions before any future PE-Reborn device integration

- Verify actual mondrian / SM8475 motor (LRA or EV_FF), Linux `/dev/input/event*` names, Xiaomi firmware vendor drivers and effect tables. Do not disable working Xiaomi/Qualcomm haptics merely to follow a newer AIDL revision.
- Select **one** compatible Vibrator AIDL HAL and frozen interface version, build/verify actual Android17 Soong dependencies, VINTF/FCM and SELinux; avoid duplicate `IVibrator/default` or global `excluded-input-devices.xml` conflicts. An independent hardware-adapted implementation can support more recent AIDL APIs only if physically implemented and accurately advertises features.
- Verify `on/off`, short taps, intensity 0–100% scaling, predefined effects, compose primitives and duration, cancellation, concurrent callbacks, external control, suspend/resume, gaming latency, haptic thermals, battery usage and UX against an actual phone.
- Hardware reference only: LF52406 `mondrian_android_device_xiaomi_sm8450-common/common.mk` selects `vendor.qti.hardware.vibrator.service` and `excluded-input-devices.xml`; **not imported into PE-Reborn**. Other ROM source is not considered an AOSP17 base.

**Status:** archived-source correctness/robustness patch, tests and CI only. No Android17 Soong build, actual Xiaomi vendor compatibility, haptic waveform calibration, VTS/CTS or physical mondrian tests. Device-tree bring-up is a separate later task. Active default manifest stays AOSP17 and does not import this source.
