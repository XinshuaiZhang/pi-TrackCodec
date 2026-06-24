#include <pybind11/pybind11.h>
#include <pybind11/numpy.h>
#include <pybind11/stl.h>
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <deque>
#include <limits>
#include <memory>
#include <stdexcept>
#include <string>
#include <tuple>
#include <unordered_map>
#include <unordered_set>
#include <utility>
#include <vector>

namespace py = pybind11;

template <typename T>
static py::array_t<T> to_py_array_copy(const std::vector<T>& values) {
    auto out = py::array_t<T>(values.size());
    if (!values.empty()) {
        std::memcpy(out.mutable_data(), values.data(), values.size() * sizeof(T));
    }
    return out;
}

static inline std::uint32_t zigzag_encode_i32(std::int32_t value) {
    return (static_cast<std::uint32_t>(value) << 1U) ^ static_cast<std::uint32_t>(value >> 31);
}

static inline std::size_t bit_width_u64(std::uint64_t value) {
    if (value == 0) {
        return 1;
    }
    std::size_t width = 0;
    while (value != 0) {
        ++width;
        value >>= 1U;
    }
    return width;
}

static inline std::size_t estimate_bitpack_bytes(std::size_t count, std::uint64_t max_value) {
    if (count == 0) {
        return 0;
    }
    const std::size_t bit_width = bit_width_u64(max_value);
    return 8 + ((bit_width * count + 7) / 8);
}

py::bytes pack_uint32_bits(
    py::array_t<std::uint32_t, py::array::c_style | py::array::forcecast> arr,
    int bit_width
) {
    if (bit_width < 1 || bit_width > 32) {
        throw std::runtime_error("Unsupported uint32 bit width");
    }
    auto buf = arr.request();
    if (buf.ndim != 1) {
        throw std::runtime_error("pack_uint32_bits expects a 1D array");
    }
    const auto count = static_cast<std::size_t>(buf.shape[0]);
    const auto* data = static_cast<const std::uint32_t*>(buf.ptr);
    if (count == 0) {
        return py::bytes();
    }
    if (bit_width == 32) {
        return py::bytes(reinterpret_cast<const char*>(data), count * sizeof(std::uint32_t));
    }

    const std::size_t total_bytes = (count * static_cast<std::size_t>(bit_width) + 7U) / 8U;
    const std::size_t word_count = ((total_bytes + 7U) / 8U) + 1U;
    std::vector<std::uint64_t> words(word_count, static_cast<std::uint64_t>(0));
    const std::uint64_t mask = (static_cast<std::uint64_t>(1) << bit_width) - 1U;

    {
        py::gil_scoped_release release;
        for (std::size_t i = 0; i < count; ++i) {
            const std::uint64_t value = static_cast<std::uint64_t>(data[i]) & mask;
            const std::uint64_t bit_pos = static_cast<std::uint64_t>(i) * static_cast<std::uint64_t>(bit_width);
            const std::size_t word_idx = static_cast<std::size_t>(bit_pos >> 6U);
            const std::uint32_t shift = static_cast<std::uint32_t>(bit_pos & 63U);
            words[word_idx] |= (value << shift);
            if (shift + static_cast<std::uint32_t>(bit_width) > 64U) {
                words[word_idx + 1U] |= (value >> (64U - shift));
            }
        }
    }

    return py::bytes(reinterpret_cast<const char*>(words.data()), total_bytes);
}

py::bytes pack_small_uint(
    py::array_t<std::uint8_t, py::array::c_style | py::array::forcecast> arr,
    int bits
) {
    if (!(bits == 1 || bits == 2 || bits == 4 || bits == 8)) {
        throw std::runtime_error("Unsupported small-uint bit width");
    }
    auto buf = arr.request();
    if (buf.ndim != 1) {
        throw std::runtime_error("pack_small_uint expects a 1D array");
    }
    const auto count = static_cast<std::size_t>(buf.shape[0]);
    const auto* data = static_cast<const std::uint8_t*>(buf.ptr);
    if (count == 0) {
        return py::bytes();
    }
    if (bits == 8) {
        return py::bytes(reinterpret_cast<const char*>(data), count * sizeof(std::uint8_t));
    }
    const std::uint8_t mask = static_cast<std::uint8_t>((1U << bits) - 1U);
    const std::size_t per_byte = 8U / static_cast<std::size_t>(bits);
    std::vector<std::uint8_t> out((count + per_byte - 1U) / per_byte, static_cast<std::uint8_t>(0));
    {
        py::gil_scoped_release release;
        for (std::size_t i = 0; i < count; ++i) {
            out[i / per_byte] |= static_cast<std::uint8_t>((data[i] & mask) << ((i % per_byte) * bits));
        }
    }
    return py::bytes(reinterpret_cast<const char*>(out.data()), out.size());
}

py::array_t<std::uint32_t> unpack_uint32_bits(
    py::buffer payload,
    int bit_width,
    std::size_t count
) {
    if (bit_width < 1 || bit_width > 32) {
        throw std::runtime_error("Unsupported uint32 bit width");
    }
    auto payload_info = payload.request();
    if (payload_info.ndim != 1 || payload_info.itemsize != 1) {
        throw std::runtime_error("unpack_uint32_bits expects a byte buffer");
    }
    const auto* raw = static_cast<const std::uint8_t*>(payload_info.ptr);
    const std::size_t raw_size = static_cast<std::size_t>(payload_info.shape[0]);
    if (count == 0) {
        if (raw_size != 0) {
            throw std::runtime_error("Corrupt uint32 bitpack stream: expected empty payload for zero count");
        }
        return py::array_t<std::uint32_t>(0);
    }
    const std::size_t expected_size = (count * static_cast<std::size_t>(bit_width) + 7U) / 8U;
    if (raw_size != expected_size) {
        throw std::runtime_error("Corrupt uint32 bitpack stream: encoded byte length does not match count and bit width");
    }

    auto out = py::array_t<std::uint32_t>(count);
    auto out_buf = out.request();
    auto* out_ptr = static_cast<std::uint32_t*>(out_buf.ptr);

    if (bit_width == 32) {
        const std::size_t available = raw_size / sizeof(std::uint32_t);
        const std::size_t copy_count = std::min(count, available);
        {
            py::gil_scoped_release release;
            if (copy_count > 0) {
                std::memcpy(out_ptr, raw, copy_count * sizeof(std::uint32_t));
            }
            if (copy_count < count) {
                std::memset(out_ptr + copy_count, 0, (count - copy_count) * sizeof(std::uint32_t));
            }
        }
        return out;
    }

    const std::size_t word_count = ((raw_size + 7U) / 8U) + 1U;
    std::vector<std::uint64_t> words(word_count, static_cast<std::uint64_t>(0));
    {
        py::gil_scoped_release release;
        if (raw_size > 0) {
            std::memcpy(words.data(), raw, raw_size);
        }
        const std::uint64_t mask = (static_cast<std::uint64_t>(1) << bit_width) - 1U;
        for (std::size_t i = 0; i < count; ++i) {
            const std::uint64_t bit_pos = static_cast<std::uint64_t>(i) * static_cast<std::uint64_t>(bit_width);
            const std::size_t word_idx = static_cast<std::size_t>(bit_pos >> 6U);
            const std::uint32_t shift = static_cast<std::uint32_t>(bit_pos & 63U);
            std::uint64_t value = words[word_idx] >> shift;
            if (shift + static_cast<std::uint32_t>(bit_width) > 64U) {
                value |= words[word_idx + 1U] << (64U - shift);
            }
            out_ptr[i] = static_cast<std::uint32_t>(value & mask);
        }
    }
    return out;
}

py::array_t<std::uint8_t> unpack_small_uint(
    py::buffer payload,
    int bits,
    std::size_t count
) {
    if (!(bits == 1 || bits == 2 || bits == 4 || bits == 8)) {
        throw std::runtime_error("Unsupported small-uint bit width");
    }
    auto payload_info = payload.request();
    if (payload_info.ndim != 1 || payload_info.itemsize != 1) {
        throw std::runtime_error("unpack_small_uint expects a byte buffer");
    }
    const auto* raw = static_cast<const std::uint8_t*>(payload_info.ptr);
    const std::size_t raw_size = static_cast<std::size_t>(payload_info.shape[0]);
    if (count == 0) {
        if (raw_size != 0) {
            throw std::runtime_error("Corrupt small-uint stream: expected empty payload for zero count");
        }
        return py::array_t<std::uint8_t>(0);
    }
    auto out = py::array_t<std::uint8_t>(count);
    auto out_buf = out.request();
    auto* out_ptr = static_cast<std::uint8_t*>(out_buf.ptr);
    if (bits == 8) {
        if (raw_size != count) {
            throw std::runtime_error("Corrupt small-uint stream: encoded byte length does not match count and bit width");
        }
        {
            py::gil_scoped_release release;
            if (count > 0) {
                std::memcpy(out_ptr, raw, count * sizeof(std::uint8_t));
            }
        }
        return out;
    }
    const std::size_t expected_size = (count * static_cast<std::size_t>(bits) + 7U) / 8U;
    if (raw_size != expected_size) {
        throw std::runtime_error("Corrupt small-uint stream: encoded byte length does not match count and bit width");
    }
    const std::uint8_t mask = static_cast<std::uint8_t>((1U << bits) - 1U);
    const std::size_t per_byte = 8U / static_cast<std::size_t>(bits);
    {
        py::gil_scoped_release release;
        for (std::size_t i = 0; i < count; ++i) {
            out_ptr[i] = static_cast<std::uint8_t>((raw[i / per_byte] >> ((i % per_byte) * bits)) & mask);
        }
    }
    return out;
}

py::array_t<std::uint32_t> zero_rle_encode_uint32(
    py::array_t<std::uint32_t, py::array::c_style | py::array::forcecast> arr,
    std::size_t min_run
) {
    auto buf = arr.request();
    if (buf.ndim != 1) {
        throw std::runtime_error("zero_rle_encode_uint32 expects a 1D array");
    }
    const auto count = static_cast<std::size_t>(buf.shape[0]);
    const auto* data = static_cast<const std::uint32_t*>(buf.ptr);
    std::vector<std::uint32_t> out;
    out.reserve(count);
    {
        py::gil_scoped_release release;
        std::size_t i = 0;
        while (i < count) {
            if (data[i] == 0U) {
                std::size_t j = i + 1U;
                while (j < count && data[j] == 0U) {
                    ++j;
                }
                const std::size_t run = j - i;
                if (run >= min_run) {
                    out.push_back(0U);
                    out.push_back(static_cast<std::uint32_t>(run));
                } else {
                    for (std::size_t k = 0; k < run; ++k) {
                        out.push_back(1U);
                    }
                }
                i = j;
                continue;
            }
            if (data[i] == std::numeric_limits<std::uint32_t>::max()) {
                throw std::overflow_error("zero-RLE uint32 cannot encode UINT32_MAX without ambiguity");
            }
            out.push_back(static_cast<std::uint32_t>(data[i] + 1U));
            ++i;
        }
    }
    auto result = py::array_t<std::uint32_t>(out.size());
    auto result_buf = result.request();
    auto* result_ptr = static_cast<std::uint32_t*>(result_buf.ptr);
    if (!out.empty()) {
        std::memcpy(result_ptr, out.data(), out.size() * sizeof(std::uint32_t));
    }
    return result;
}

py::array_t<std::uint32_t> zero_rle_decode_uint32(
    py::array_t<std::uint32_t, py::array::c_style | py::array::forcecast> arr
) {
    auto buf = arr.request();
    if (buf.ndim != 1) {
        throw std::runtime_error("zero_rle_decode_uint32 expects a 1D array");
    }
    const auto count = static_cast<std::size_t>(buf.shape[0]);
    const auto* data = static_cast<const std::uint32_t*>(buf.ptr);
    std::vector<std::uint32_t> out;
    out.reserve(count);
    {
        py::gil_scoped_release release;
        std::size_t i = 0;
        while (i < count) {
            const std::uint32_t token = data[i];
            if (token == 0U) {
                if (i + 1U >= count) {
                    throw std::runtime_error("Corrupt zero-RLE stream: missing run length");
                }
                const std::size_t run = static_cast<std::size_t>(data[i + 1U]);
                out.insert(out.end(), run, 0U);
                i += 2U;
                continue;
            }
            out.push_back(token - 1U);
            ++i;
        }
    }
    auto result = py::array_t<std::uint32_t>(out.size());
    auto result_buf = result.request();
    auto* result_ptr = static_cast<std::uint32_t*>(result_buf.ptr);
    if (!out.empty()) {
        std::memcpy(result_ptr, out.data(), out.size() * sizeof(std::uint32_t));
    }
    return result;
}

static inline std::int64_t round_even_to_i64(double value) {
    return static_cast<std::int64_t>(std::nearbyint(value));
}

static inline std::size_t estimate_int64_main_bytes_core(
    const std::int64_t* data,
    std::size_t count
) {
    constexpr std::int64_t INT32_MIN_LL = static_cast<std::int64_t>(INT32_MIN);
    constexpr std::int64_t INT32_MAX_LL = static_cast<std::int64_t>(INT32_MAX);

    std::uint32_t dense_max = 0;
    std::uint32_t sparse_idx_max = 0;
    std::uint32_t sparse_val_max = 0;
    std::size_t nonzero_count = 0;

    std::uint32_t overflow_idx_max = 0;
    std::size_t overflow_count = 0;

    for (std::size_t i = 0; i < count; ++i) {
        const std::int64_t value64 = data[i];
        std::int32_t main_value = 0;
        if (value64 < INT32_MIN_LL || value64 > INT32_MAX_LL) {
            ++overflow_count;
            overflow_idx_max = static_cast<std::uint32_t>(i);
        } else {
            main_value = static_cast<std::int32_t>(value64);
        }
        const std::uint32_t zig = zigzag_encode_i32(main_value);
        if (zig > dense_max) {
            dense_max = zig;
        }
        if (main_value != 0) {
            ++nonzero_count;
            sparse_idx_max = static_cast<std::uint32_t>(i);
            if (zig > sparse_val_max) {
                sparse_val_max = zig;
            }
        }
    }

    const std::size_t dense = estimate_bitpack_bytes(count, static_cast<std::uint64_t>(dense_max));
    const std::size_t sparse = (nonzero_count == 0)
        ? 8
        : (12
           + estimate_bitpack_bytes(nonzero_count, static_cast<std::uint64_t>(sparse_idx_max))
           + estimate_bitpack_bytes(nonzero_count, static_cast<std::uint64_t>(sparse_val_max)));
    const std::size_t main_est = dense < sparse ? dense : sparse;
    const std::size_t overflow_idx_est = overflow_count == 0 ? 0 : estimate_bitpack_bytes(overflow_count, static_cast<std::uint64_t>(overflow_idx_max));
    const std::size_t overflow_val_est = overflow_count == 0 ? 0 : (8 + 8 * overflow_count);
    return main_est + overflow_idx_est + overflow_val_est;
}

static inline std::size_t estimate_scaled_full_bytes_core(
    const double* data,
    std::size_t count,
    double precision
) {
    constexpr std::int64_t INT32_MIN_LL = static_cast<std::int64_t>(INT32_MIN);
    constexpr std::int64_t INT32_MAX_LL = static_cast<std::int64_t>(INT32_MAX);

    std::uint32_t dense_max = 0;
    std::uint32_t sparse_idx_max = 0;
    std::uint32_t sparse_val_max = 0;
    std::size_t nonzero_count = 0;
    std::uint32_t overflow_idx_max = 0;
    std::size_t overflow_count = 0;

    for (std::size_t i = 0; i < count; ++i) {
        const std::int64_t value64 = round_even_to_i64(data[i] * precision);
        std::int32_t main_value = 0;
        if (value64 < INT32_MIN_LL || value64 > INT32_MAX_LL) {
            ++overflow_count;
            overflow_idx_max = static_cast<std::uint32_t>(i);
        } else {
            main_value = static_cast<std::int32_t>(value64);
        }
        const std::uint32_t zig = zigzag_encode_i32(main_value);
        if (zig > dense_max) {
            dense_max = zig;
        }
        if (main_value != 0) {
            ++nonzero_count;
            sparse_idx_max = static_cast<std::uint32_t>(i);
            if (zig > sparse_val_max) {
                sparse_val_max = zig;
            }
        }
    }

    const std::size_t dense = estimate_bitpack_bytes(count, static_cast<std::uint64_t>(dense_max));
    const std::size_t sparse = (nonzero_count == 0)
        ? 8
        : (12
           + estimate_bitpack_bytes(nonzero_count, static_cast<std::uint64_t>(sparse_idx_max))
           + estimate_bitpack_bytes(nonzero_count, static_cast<std::uint64_t>(sparse_val_max)));
    const std::size_t main_est = dense < sparse ? dense : sparse;
    const std::size_t overflow_idx_est =
        overflow_count == 0 ? 0 : estimate_bitpack_bytes(overflow_count, static_cast<std::uint64_t>(overflow_idx_max));
    const std::size_t overflow_val_est = overflow_count == 0 ? 0 : (8 + 8 * overflow_count);
    return main_est + overflow_idx_est + overflow_val_est;
}

static inline std::size_t estimate_scaled_delta_bytes_core(
    const double* curr,
    std::size_t curr_len,
    const double* ref,
    std::size_t ref_len,
    std::size_t encoded_len,
    double precision
) {
    constexpr std::int64_t INT32_MIN_LL = static_cast<std::int64_t>(INT32_MIN);
    constexpr std::int64_t INT32_MAX_LL = static_cast<std::int64_t>(INT32_MAX);

    std::uint32_t dense_max = 0;
    std::uint32_t sparse_idx_max = 0;
    std::uint32_t sparse_val_max = 0;
    std::size_t nonzero_count = 0;
    std::uint32_t overflow_idx_max = 0;
    std::size_t overflow_count = 0;

    for (std::size_t i = 0; i < encoded_len; ++i) {
        const std::int64_t curr64 = i < curr_len ? round_even_to_i64(curr[i] * precision) : 0;
        const std::int64_t ref64 = i < ref_len ? round_even_to_i64(ref[i] * precision) : 0;
        const std::int64_t value64 = curr64 - ref64;
        std::int32_t main_value = 0;
        if (value64 < INT32_MIN_LL || value64 > INT32_MAX_LL) {
            ++overflow_count;
            overflow_idx_max = static_cast<std::uint32_t>(i);
        } else {
            main_value = static_cast<std::int32_t>(value64);
        }
        const std::uint32_t zig = zigzag_encode_i32(main_value);
        if (zig > dense_max) {
            dense_max = zig;
        }
        if (main_value != 0) {
            ++nonzero_count;
            sparse_idx_max = static_cast<std::uint32_t>(i);
            if (zig > sparse_val_max) {
                sparse_val_max = zig;
            }
        }
    }

    const std::size_t dense = estimate_bitpack_bytes(encoded_len, static_cast<std::uint64_t>(dense_max));
    const std::size_t sparse = (nonzero_count == 0)
        ? 8
        : (12
           + estimate_bitpack_bytes(nonzero_count, static_cast<std::uint64_t>(sparse_idx_max))
           + estimate_bitpack_bytes(nonzero_count, static_cast<std::uint64_t>(sparse_val_max)));
    const std::size_t main_est = dense < sparse ? dense : sparse;
    const std::size_t overflow_idx_est =
        overflow_count == 0 ? 0 : estimate_bitpack_bytes(overflow_count, static_cast<std::uint64_t>(overflow_idx_max));
    const std::size_t overflow_val_est = overflow_count == 0 ? 0 : (8 + 8 * overflow_count);
    return main_est + overflow_idx_est + overflow_val_est;
}

static inline double mean_abs_padded_delta(
    const double* curr,
    std::size_t curr_len,
    const double* ref,
    std::size_t ref_len,
    std::size_t encoded_len
) {
    if (encoded_len == 0) {
        return 0.0;
    }
    double total = 0.0;
    for (std::size_t i = 0; i < encoded_len; ++i) {
        const double curr_v = i < curr_len ? curr[i] : 0.0;
        const double ref_v = i < ref_len ? ref[i] : 0.0;
        total += std::fabs(curr_v - ref_v);
    }
    return total / static_cast<double>(encoded_len);
}

static inline int delta_encoded_length_core(
    std::size_t curr_len,
    std::size_t ref_len,
    bool use_max_ref_curr
) {
    return static_cast<int>(use_max_ref_curr ? std::max(curr_len, ref_len) : curr_len);
}

static inline std::size_t estimate_signed_residual_bytes_core(
    const std::int32_t* data,
    std::size_t count
) {
    if (count == 0) {
        return 0;
    }
    std::uint32_t dense_max = 0;
    std::uint32_t sparse_idx_max = 0;
    std::uint32_t sparse_val_max = 0;
    std::size_t nonzero_count = 0;

    for (std::size_t i = 0; i < count; ++i) {
        const std::int32_t value = data[i];
        const std::uint32_t zig = zigzag_encode_i32(value);
        if (zig > dense_max) {
            dense_max = zig;
        }
        if (value != 0) {
            ++nonzero_count;
            if (static_cast<std::uint32_t>(i) > sparse_idx_max) {
                sparse_idx_max = static_cast<std::uint32_t>(i);
            }
            if (zig > sparse_val_max) {
                sparse_val_max = zig;
            }
        }
    }

    const std::size_t dense = estimate_bitpack_bytes(count, static_cast<std::uint64_t>(dense_max));
    if (nonzero_count == 0) {
        return 8;
    }
    const std::size_t sparse =
        12
        + estimate_bitpack_bytes(nonzero_count, static_cast<std::uint64_t>(sparse_idx_max))
        + estimate_bitpack_bytes(nonzero_count, static_cast<std::uint64_t>(sparse_val_max));
    return dense < sparse ? dense : sparse;
}

static inline std::size_t estimate_mz_model_bytes_core(
    std::int32_t offset,
    std::int32_t step,
    const std::int32_t* residual,
    std::size_t count
) {
    const std::int32_t offset_arr[1] = {offset};
    const std::int32_t step_arr[1] = {step};
    return 12
        + estimate_signed_residual_bytes_core(offset_arr, 1)
        + estimate_signed_residual_bytes_core(step_arr, 1)
        + estimate_signed_residual_bytes_core(residual, count);
}

static inline std::size_t estimate_uint64_anchor_bytes_core(std::uint64_t value) {
    if (value <= UINT32_MAX) {
        return 8U;
    }
    const std::size_t main_cost = estimate_bitpack_bytes(1, 0U);
    return main_cost + estimate_bitpack_bytes(1, 0U) + (8U + 8U);
}

static inline std::size_t estimate_raw_mz_local_bytes_u64_core(
    const std::uint64_t* data,
    std::size_t count
) {
    if (count == 0) {
        return 4U;
    }
    const std::size_t first_cost = estimate_uint64_anchor_bytes_core(data[0]);
    if (count == 1) {
        return 4U + first_cost;
    }
    std::uint64_t max_delta = 0;
    for (std::size_t i = 1; i < count; ++i) {
        if (data[i] < data[i - 1]) {
            throw std::runtime_error("select_mz_representation_u64: quantized m/z must be nondecreasing");
        }
        const std::uint64_t delta = data[i] - data[i - 1];
        if (delta > UINT32_MAX) {
            throw std::runtime_error(
                "select_mz_representation_u64: raw m/z delta exceeds uint32 range; strict overflow sidecar only covers anchors"
            );
        }
        if (delta > max_delta) {
            max_delta = delta;
        }
    }
    return 4U + first_cost + estimate_bitpack_bytes(count - 1U, max_delta);
}

static inline std::vector<int> expand_small_int_candidates_core(
    const std::vector<int>& candidates,
    int radius
) {
    std::vector<int> out;
    out.reserve(candidates.size() * static_cast<std::size_t>(2 * radius + 1));
    std::unordered_set<int> seen;
    seen.reserve(candidates.size() * static_cast<std::size_t>(2 * radius + 1));
    for (const auto value : candidates) {
        for (int delta = -radius; delta <= radius; ++delta) {
            const int v = value + delta;
            if (seen.insert(v).second) {
                out.push_back(v);
            }
        }
    }
    return out;
}

static inline std::uint64_t pack_int_pair_key(int first, int second) {
    const auto hi = static_cast<std::uint32_t>(first);
    const auto lo = static_cast<std::uint32_t>(second);
    return (static_cast<std::uint64_t>(hi) << 32U) | static_cast<std::uint64_t>(lo);
}

std::size_t estimate_uint32_bitpack_bytes(py::array_t<std::uint32_t, py::array::c_style | py::array::forcecast> arr) {
    auto buf = arr.request();
    const auto count = static_cast<std::size_t>(buf.size);
    if (count == 0) {
        return 0;
    }
    const auto* data = static_cast<const std::uint32_t*>(buf.ptr);
    std::uint32_t max_value = 0;
    {
        py::gil_scoped_release release;
        for (std::size_t i = 0; i < count; ++i) {
            if (data[i] > max_value) {
                max_value = data[i];
            }
        }
    }
    return estimate_bitpack_bytes(count, static_cast<std::uint64_t>(max_value));
}

std::size_t estimate_signed_residual_bytes(py::array_t<std::int32_t, py::array::c_style | py::array::forcecast> arr) {
    auto buf = arr.request();
    const auto count = static_cast<std::size_t>(buf.size);
    if (count == 0) {
        return 0;
    }
    const auto* data = static_cast<const std::int32_t*>(buf.ptr);
    std::size_t out = 0;
    {
        py::gil_scoped_release release;
        out = estimate_signed_residual_bytes_core(data, count);
    }
    return out;
}

std::size_t estimate_raw_mz_local_bytes(py::array_t<std::uint32_t, py::array::c_style | py::array::forcecast> arr) {
    auto buf = arr.request();
    const auto count = static_cast<std::size_t>(buf.size);
    if (count == 0) {
        return 4;
    }
    const std::size_t first_cost = 8;
    if (count == 1) {
        return 4 + first_cost;
    }
    const auto* data = static_cast<const std::uint32_t*>(buf.ptr);
    std::uint64_t max_delta = 0;
    std::uint64_t prev = static_cast<std::uint64_t>(data[0]);
    {
        py::gil_scoped_release release;
        for (std::size_t i = 1; i < count; ++i) {
            const std::uint64_t curr = static_cast<std::uint64_t>(data[i]);
            const std::uint64_t delta = curr - prev;
            if (delta > max_delta) {
                max_delta = delta;
            }
            prev = curr;
        }
    }
    return 4 + first_cost + estimate_bitpack_bytes(count - 1, max_delta);
}

std::size_t estimate_int64_main_bytes(py::array_t<std::int64_t, py::array::c_style | py::array::forcecast> arr) {
    auto buf = arr.request();
    const auto count = static_cast<std::size_t>(buf.size);
    if (count == 0) {
        return 0;
    }
    const auto* data = static_cast<const std::int64_t*>(buf.ptr);
    std::size_t out = 0;
    {
        py::gil_scoped_release release;
        out = estimate_int64_main_bytes_core(data, count);
    }
    return out;
}

std::size_t estimate_delta_int64_bytes(
    py::array_t<std::int64_t, py::array::c_style | py::array::forcecast> curr,
    py::array_t<std::int64_t, py::array::c_style | py::array::forcecast> ref
) {
    auto curr_buf = curr.request();
    auto ref_buf = ref.request();
    const auto count = static_cast<std::size_t>(curr_buf.size);
    if (count != static_cast<std::size_t>(ref_buf.size)) {
        throw std::runtime_error("estimate_delta_int64_bytes length mismatch");
    }
    if (count == 0) {
        return 0;
    }
    const auto* curr_ptr = static_cast<const std::int64_t*>(curr_buf.ptr);
    const auto* ref_ptr = static_cast<const std::int64_t*>(ref_buf.ptr);
    std::vector<std::int64_t> delta(count);
    std::size_t out = 0;
    {
        py::gil_scoped_release release;
        for (std::size_t i = 0; i < count; ++i) {
            delta[i] = curr_ptr[i] - ref_ptr[i];
        }
        out = estimate_int64_main_bytes_core(delta.data(), count);
    }
    return out;
}

std::size_t estimate_delta2_int64_bytes(
    py::array_t<std::int64_t, py::array::c_style | py::array::forcecast> curr,
    py::array_t<std::int64_t, py::array::c_style | py::array::forcecast> prev,
    py::array_t<std::int64_t, py::array::c_style | py::array::forcecast> prev2
) {
    auto curr_buf = curr.request();
    auto prev_buf = prev.request();
    auto prev2_buf = prev2.request();
    const auto count = static_cast<std::size_t>(curr_buf.size);
    if (count != static_cast<std::size_t>(prev_buf.size) || count != static_cast<std::size_t>(prev2_buf.size)) {
        throw std::runtime_error("estimate_delta2_int64_bytes length mismatch");
    }
    if (count == 0) {
        return 0;
    }
    const auto* curr_ptr = static_cast<const std::int64_t*>(curr_buf.ptr);
    const auto* prev_ptr = static_cast<const std::int64_t*>(prev_buf.ptr);
    const auto* prev2_ptr = static_cast<const std::int64_t*>(prev2_buf.ptr);
    std::vector<std::int64_t> delta(count);
    std::size_t out = 0;
    {
        py::gil_scoped_release release;
        for (std::size_t i = 0; i < count; ++i) {
            delta[i] = curr_ptr[i] - (2 * prev_ptr[i]) + prev2_ptr[i];
        }
        out = estimate_int64_main_bytes_core(delta.data(), count);
    }
    return out;
}

py::tuple strategy_a_island_features(
    py::array_t<double, py::array::c_style | py::array::forcecast> mz_arr,
    py::array_t<double, py::array::c_style | py::array::forcecast> int_arr,
    std::size_t min_points
) {
    auto mz_buf = mz_arr.request();
    auto int_buf = int_arr.request();
    const auto count = static_cast<std::size_t>(int_buf.size);
    if (count != static_cast<std::size_t>(mz_buf.size)) {
        throw std::runtime_error("strategy_a_island_features length mismatch");
    }
    const auto* mz = static_cast<const double*>(mz_buf.ptr);
    const auto* intensity = static_cast<const double*>(int_buf.ptr);

    std::vector<std::uint32_t> starts;
    std::vector<std::uint32_t> ends;
    std::vector<double> mz_starts;
    std::vector<double> mz_steps;
    std::vector<double> centers;
    std::vector<double> max_values;

    std::size_t i = 0;
    {
        py::gil_scoped_release release;
        while (i < count) {
            while (i < count && !(intensity[i] > 0.0)) {
                ++i;
            }
            if (i >= count) {
                break;
            }
            const std::size_t start = i;
            std::size_t end = i + 1;
            double weighted_sum = mz[i] * intensity[i];
            double total = intensity[i];
            double max_intensity = intensity[i];
            while (end < count && intensity[end] > 0.0) {
                weighted_sum += mz[end] * intensity[end];
                total += intensity[end];
                if (intensity[end] > max_intensity) {
                    max_intensity = intensity[end];
                }
                ++end;
            }
            if (end - start >= min_points) {
                starts.push_back(static_cast<std::uint32_t>(start));
                ends.push_back(static_cast<std::uint32_t>(end));
                mz_starts.push_back(mz[start]);
                mz_steps.push_back((end - start) > 1 ? (mz[start + 1] - mz[start]) : 0.0);
                centers.push_back(total > 0.0 ? (weighted_sum / total) : mz[start]);
                max_values.push_back(max_intensity);
            }
            i = end;
        }
    }

    auto py_starts = py::array_t<std::uint32_t>(starts.size());
    auto py_ends = py::array_t<std::uint32_t>(ends.size());
    auto py_mz_starts = py::array_t<double>(mz_starts.size());
    auto py_mz_steps = py::array_t<double>(mz_steps.size());
    auto py_centers = py::array_t<double>(centers.size());
    auto py_max_values = py::array_t<double>(max_values.size());

    std::memcpy(py_starts.mutable_data(), starts.data(), starts.size() * sizeof(std::uint32_t));
    std::memcpy(py_ends.mutable_data(), ends.data(), ends.size() * sizeof(std::uint32_t));
    std::memcpy(py_mz_starts.mutable_data(), mz_starts.data(), mz_starts.size() * sizeof(double));
    std::memcpy(py_mz_steps.mutable_data(), mz_steps.data(), mz_steps.size() * sizeof(double));
    std::memcpy(py_centers.mutable_data(), centers.data(), centers.size() * sizeof(double));
    std::memcpy(py_max_values.mutable_data(), max_values.data(), max_values.size() * sizeof(double));

    return py::make_tuple(py_starts, py_ends, py_mz_starts, py_mz_steps, py_centers, py_max_values);
}

py::tuple strategy_b_island_features(
    py::array_t<double, py::array::c_style | py::array::forcecast> mz_arr,
    py::array_t<double, py::array::c_style | py::array::forcecast> int_arr,
    std::size_t min_points,
    double strategy_b_valley_ratio
) {
    auto mz_buf = mz_arr.request();
    auto int_buf = int_arr.request();
    const auto count = static_cast<std::size_t>(int_buf.size);
    if (count != static_cast<std::size_t>(mz_buf.size)) {
        throw std::runtime_error("strategy_b_island_features length mismatch");
    }
    const auto* mz = static_cast<const double*>(mz_buf.ptr);
    const auto* intensity = static_cast<const double*>(int_buf.ptr);

    std::vector<std::uint32_t> starts;
    std::vector<std::uint32_t> ends;
    std::vector<double> mz_starts;
    std::vector<double> mz_steps;
    std::vector<double> centers;
    std::vector<double> max_values;

    {
        py::gil_scoped_release release;
        if (count < 3) {
            const std::size_t min_pts_a = std::max<std::size_t>(1, min_points);
            std::size_t i = 0;
            while (i < count) {
                while (i < count && !(intensity[i] > 0.0)) {
                    ++i;
                }
                if (i >= count) {
                    break;
                }
                const std::size_t start = i;
                std::size_t end = i + 1;
                double weighted_sum = mz[i] * intensity[i];
                double total = intensity[i];
                double max_intensity = intensity[i];
                while (end < count && intensity[end] > 0.0) {
                    weighted_sum += mz[end] * intensity[end];
                    total += intensity[end];
                    if (intensity[end] > max_intensity) {
                        max_intensity = intensity[end];
                    }
                    ++end;
                }
                if (end - start >= min_pts_a) {
                    starts.push_back(static_cast<std::uint32_t>(start));
                    ends.push_back(static_cast<std::uint32_t>(end));
                    mz_starts.push_back(mz[start]);
                    mz_steps.push_back((end - start) > 1 ? (mz[start + 1] - mz[start]) : 0.0);
                    centers.push_back(total > 0.0 ? (weighted_sum / total) : mz[start]);
                    max_values.push_back(max_intensity);
                }
                i = end;
            }
        } else {
            const std::size_t min_pts = std::max<std::size_t>(min_points, 3);
            const double valley_ratio = std::max(0.0, strategy_b_valley_ratio);

            std::vector<std::size_t> split_pts;
            split_pts.reserve(64);
            split_pts.push_back(0);

            // Match scipy.signal.argrelmin(..., order=2, mode="clip"):
            // strict comparisons against clipped +/-1 and +/-2 neighbors.
            // Endpoints can never pass because one clipped neighbor is self,
            // but near-edge interior points may pass on short arrays.
            if (count >= 3) {
                for (std::size_t vi = 1; vi + 1 < count; ++vi) {
                    const double v = intensity[vi];
                    bool is_min = true;
                    for (std::size_t order = 1; order <= 2; ++order) {
                        const std::size_t left_idx = (vi >= order) ? (vi - order) : 0;
                        const std::size_t right_idx = ((vi + order) < count) ? (vi + order) : (count - 1);
                        if (!(v < intensity[left_idx] && v < intensity[right_idx])) {
                            is_min = false;
                            break;
                        }
                    }
                    if (!is_min) {
                        continue;
                    }
                    const std::size_t left_start = (vi > 5) ? (vi - 5) : 0;
                    double left_max = intensity[left_start];
                    for (std::size_t k = left_start + 1; k < vi; ++k) {
                        if (intensity[k] > left_max) {
                            left_max = intensity[k];
                        }
                    }
                    const std::size_t right_end = std::min<std::size_t>(count, vi + 6);
                    double right_max = intensity[vi + 1];
                    for (std::size_t k = vi + 2; k < right_end; ++k) {
                        if (intensity[k] > right_max) {
                            right_max = intensity[k];
                        }
                    }
                    if (v < std::max(left_max, right_max) * valley_ratio) {
                        split_pts.push_back(vi);
                    }
                }
            }
            split_pts.push_back(count);

            for (std::size_t j = 0; j + 1 < split_pts.size(); ++j) {
                const std::size_t s = split_pts[j];
                const std::size_t e = split_pts[j + 1];
                if (e <= s || (e - s) < min_pts) {
                    continue;
                }
                std::size_t first_nz = e;
                std::size_t last_nz_plus_one = s;
                for (std::size_t k = s; k < e; ++k) {
                    if (intensity[k] > 0.0) {
                        if (first_nz == e) {
                            first_nz = k;
                        }
                        last_nz_plus_one = k + 1;
                    }
                }
                if (first_nz == e || last_nz_plus_one <= first_nz) {
                    continue;
                }
                if (last_nz_plus_one - first_nz >= min_pts) {
                    starts.push_back(static_cast<std::uint32_t>(first_nz));
                    ends.push_back(static_cast<std::uint32_t>(last_nz_plus_one));
                }
            }

            if (!starts.empty()) {
                std::vector<std::uint8_t> covered(count, static_cast<std::uint8_t>(0));
                for (std::size_t idx = 0; idx < starts.size(); ++idx) {
                    const auto s = static_cast<std::size_t>(starts[idx]);
                    const auto e = static_cast<std::size_t>(ends[idx]);
                    for (std::size_t k = s; k < e; ++k) {
                        covered[k] = static_cast<std::uint8_t>(1);
                    }
                }
                std::size_t k = 0;
                while (k < count) {
                    while (k < count && (!(intensity[k] > 0.0) || covered[k])) {
                        ++k;
                    }
                    if (k >= count) {
                        break;
                    }
                    const std::size_t extra_start = k;
                    while (k < count && intensity[k] > 0.0 && !covered[k]) {
                        ++k;
                    }
                    starts.push_back(static_cast<std::uint32_t>(extra_start));
                    ends.push_back(static_cast<std::uint32_t>(k));
                }
                std::vector<std::size_t> order(starts.size());
                for (std::size_t idx = 0; idx < order.size(); ++idx) {
                    order[idx] = idx;
                }
                std::sort(order.begin(), order.end(), [&](std::size_t a, std::size_t b) {
                    return starts[a] < starts[b];
                });
                std::vector<std::uint32_t> sorted_starts;
                std::vector<std::uint32_t> sorted_ends;
                sorted_starts.reserve(starts.size());
                sorted_ends.reserve(ends.size());
                for (const auto idx : order) {
                    sorted_starts.push_back(starts[idx]);
                    sorted_ends.push_back(ends[idx]);
                }
                starts.swap(sorted_starts);
                ends.swap(sorted_ends);
            }

            mz_starts.reserve(starts.size());
            mz_steps.reserve(starts.size());
            centers.reserve(starts.size());
            max_values.reserve(starts.size());
            for (std::size_t idx = 0; idx < starts.size(); ++idx) {
                const auto start = static_cast<std::size_t>(starts[idx]);
                const auto end = static_cast<std::size_t>(ends[idx]);
                double weighted_sum = 0.0;
                double total = 0.0;
                double max_intensity = intensity[start];
                for (std::size_t k = start; k < end; ++k) {
                    weighted_sum += mz[k] * intensity[k];
                    total += intensity[k];
                    if (intensity[k] > max_intensity) {
                        max_intensity = intensity[k];
                    }
                }
                mz_starts.push_back(mz[start]);
                mz_steps.push_back((end - start) > 1 ? (mz[start + 1] - mz[start]) : 0.0);
                centers.push_back(total > 0.0 ? (weighted_sum / total) : mz[start]);
                max_values.push_back(max_intensity);
            }
        }
    }

    auto py_starts = py::array_t<std::uint32_t>(starts.size());
    auto py_ends = py::array_t<std::uint32_t>(ends.size());
    auto py_mz_starts = py::array_t<double>(mz_starts.size());
    auto py_mz_steps = py::array_t<double>(mz_steps.size());
    auto py_centers = py::array_t<double>(centers.size());
    auto py_max_values = py::array_t<double>(max_values.size());

    if (!starts.empty()) {
        std::memcpy(py_starts.mutable_data(), starts.data(), starts.size() * sizeof(std::uint32_t));
        std::memcpy(py_ends.mutable_data(), ends.data(), ends.size() * sizeof(std::uint32_t));
        std::memcpy(py_mz_starts.mutable_data(), mz_starts.data(), mz_starts.size() * sizeof(double));
        std::memcpy(py_mz_steps.mutable_data(), mz_steps.data(), mz_steps.size() * sizeof(double));
        std::memcpy(py_centers.mutable_data(), centers.data(), centers.size() * sizeof(double));
        std::memcpy(py_max_values.mutable_data(), max_values.data(), max_values.size() * sizeof(double));
    }

    return py::make_tuple(py_starts, py_ends, py_mz_starts, py_mz_steps, py_centers, py_max_values);
}

py::tuple build_compact_tracks_baseline(
    py::array_t<double, py::array::c_style | py::array::forcecast> center_mz,
    py::array_t<double, py::array::c_style | py::array::forcecast> mz_start,
    py::array_t<double, py::array::c_style | py::array::forcecast> mz_step,
    py::array_t<std::uint32_t, py::array::c_style | py::array::forcecast> n_points,
    py::array_t<double, py::array::c_style | py::array::forcecast> max_intensity,
    py::array_t<std::uint32_t, py::array::c_style | py::array::forcecast> scan_offsets,
    double ppm_tol
) {
    auto center_buf = center_mz.request();
    auto start_buf = mz_start.request();
    auto step_buf = mz_step.request();
    auto npts_buf = n_points.request();
    auto max_buf = max_intensity.request();
    auto offsets_buf = scan_offsets.request();

    const auto island_count = static_cast<std::size_t>(center_buf.size);
    const bool unused_start_ok = static_cast<std::size_t>(start_buf.size) == 0 || island_count == static_cast<std::size_t>(start_buf.size);
    const bool unused_step_ok = static_cast<std::size_t>(step_buf.size) == 0 || island_count == static_cast<std::size_t>(step_buf.size);
    const bool unused_max_ok = static_cast<std::size_t>(max_buf.size) == 0 || island_count == static_cast<std::size_t>(max_buf.size);
    if (!unused_start_ok
        || !unused_step_ok
        || island_count != static_cast<std::size_t>(npts_buf.size)
        || !unused_max_ok) {
        throw std::runtime_error("build_compact_tracks_baseline island array length mismatch");
    }
    const auto scan_count_plus_one = static_cast<std::size_t>(offsets_buf.size);
    if (scan_count_plus_one == 0) {
        auto py_track_lengths = py::array_t<std::uint32_t>(0);
        auto py_order = py::array_t<std::uint32_t>(0);
        return py::make_tuple(py_track_lengths, py_order);
    }

    const auto* center_ptr = static_cast<const double*>(center_buf.ptr);
    const auto* offsets_ptr = static_cast<const std::uint32_t*>(offsets_buf.ptr);

    struct TrackState {
        std::vector<std::uint32_t> islands;
        bool finalized = false;
    };

    std::vector<TrackState> all_tracks;
    std::vector<std::size_t> finalized_track_indices;
    std::vector<std::size_t> prev_active_track_for_island;
    std::vector<std::size_t> prev_active_order;
    std::vector<std::uint32_t> track_lengths;
    std::vector<std::uint32_t> island_order;

    const auto scan_count = scan_count_plus_one - 1;
    {
    py::gil_scoped_release release;
    for (std::size_t scan_idx = 0; scan_idx < scan_count; ++scan_idx) {
        const std::size_t curr_start = static_cast<std::size_t>(offsets_ptr[scan_idx]);
        const std::size_t curr_end = static_cast<std::size_t>(offsets_ptr[scan_idx + 1]);
        const std::size_t curr_count = curr_end - curr_start;

        if (scan_idx == 0) {
            prev_active_track_for_island.resize(curr_count);
            prev_active_order.resize(curr_count);
            for (std::size_t ci = 0; ci < curr_count; ++ci) {
                all_tracks.push_back(TrackState{{static_cast<std::uint32_t>(curr_start + ci)}, false});
                prev_active_track_for_island[ci] = all_tracks.size() - 1;
                prev_active_order[ci] = all_tracks.size() - 1;
            }
            continue;
        }

        const std::size_t prev_start = static_cast<std::size_t>(offsets_ptr[scan_idx - 1]);
        const std::size_t prev_end = static_cast<std::size_t>(offsets_ptr[scan_idx]);
        const std::size_t prev_count = prev_end - prev_start;

        struct Candidate {
            double ppm;
            std::uint32_t prev_local;
            std::uint32_t curr_local;
        };
        std::vector<std::uint32_t> prev_sorted(prev_count);
        for (std::size_t i = 0; i < prev_count; ++i) {
            prev_sorted[i] = static_cast<std::uint32_t>(i);
        }
        std::sort(prev_sorted.begin(), prev_sorted.end(), [&](std::uint32_t a, std::uint32_t b) {
            return center_ptr[prev_start + a] < center_ptr[prev_start + b];
        });
        std::vector<double> prev_sorted_cmz(prev_count);
        for (std::size_t i = 0; i < prev_count; ++i) {
            prev_sorted_cmz[i] = center_ptr[prev_start + prev_sorted[i]];
        }

        std::vector<Candidate> candidates;
        candidates.reserve(curr_count * 2);
        for (std::size_t ci = 0; ci < curr_count; ++ci) {
            const std::size_t global_ci = curr_start + ci;
            const double cmz = center_ptr[global_ci];
            auto it = std::lower_bound(prev_sorted_cmz.begin(), prev_sorted_cmz.end(), cmz);
            const std::ptrdiff_t pos = it - prev_sorted_cmz.begin();
            for (int off = -1; off <= 0; ++off) {
                const std::ptrdiff_t p = pos + off;
                if (p < 0 || p >= static_cast<std::ptrdiff_t>(prev_count)) {
                    continue;
                }
                const std::size_t prev_local = static_cast<std::size_t>(prev_sorted[static_cast<std::size_t>(p)]);
                const double pmz = prev_sorted_cmz[static_cast<std::size_t>(p)];
                const double ppm = std::fabs(cmz - pmz) / pmz * 1e6;
                if (ppm < ppm_tol) {
                    candidates.push_back(Candidate{
                        ppm,
                        static_cast<std::uint32_t>(prev_local),
                        static_cast<std::uint32_t>(ci),
                    });
                }
            }
        }
        std::sort(candidates.begin(), candidates.end(), [](const Candidate& a, const Candidate& b) {
            if (a.ppm != b.ppm) {
                return a.ppm < b.ppm;
            }
            if (a.prev_local != b.prev_local) {
                return a.prev_local < b.prev_local;
            }
            return a.curr_local < b.curr_local;
        });

        std::vector<std::uint8_t> used_prev(prev_count, 0);
        std::vector<std::uint8_t> used_curr(curr_count, 0);
        std::vector<std::size_t> new_active_track_for_island(curr_count, static_cast<std::size_t>(-1));
        std::vector<std::size_t> new_active_order;
        new_active_order.reserve(curr_count);

        for (const auto& cand : candidates) {
            if (used_prev[cand.prev_local] || used_curr[cand.curr_local]) {
                continue;
            }
            used_prev[cand.prev_local] = 1;
            used_curr[cand.curr_local] = 1;
            const auto track_idx = prev_active_track_for_island[cand.prev_local];
            all_tracks[track_idx].islands.push_back(static_cast<std::uint32_t>(curr_start + cand.curr_local));
            new_active_track_for_island[cand.curr_local] = track_idx;
            new_active_order.push_back(track_idx);
        }

        for (std::size_t pi = 0; pi < prev_count; ++pi) {
            if (!used_prev[pi]) {
                const auto track_idx = prev_active_track_for_island[pi];
                if (!all_tracks[track_idx].finalized) {
                    all_tracks[track_idx].finalized = true;
                    finalized_track_indices.push_back(track_idx);
                }
            }
        }

        for (std::size_t ci = 0; ci < curr_count; ++ci) {
            if (new_active_track_for_island[ci] == static_cast<std::size_t>(-1)) {
                all_tracks.push_back(TrackState{{static_cast<std::uint32_t>(curr_start + ci)}, false});
                new_active_track_for_island[ci] = all_tracks.size() - 1;
                new_active_order.push_back(all_tracks.size() - 1);
            }
        }

        prev_active_track_for_island.swap(new_active_track_for_island);
        prev_active_order.swap(new_active_order);
    }

    for (std::size_t order_idx = 0; order_idx < prev_active_order.size(); ++order_idx) {
        const auto track_idx = prev_active_order[order_idx];
        if (!all_tracks[track_idx].finalized) {
            all_tracks[track_idx].finalized = true;
            finalized_track_indices.push_back(track_idx);
        }
    }

    track_lengths.reserve(finalized_track_indices.size());
    island_order.reserve(island_count);
    for (const auto track_idx : finalized_track_indices) {
        const auto& track = all_tracks[track_idx];
        if (track.islands.empty()) {
            continue;
        }
        track_lengths.push_back(static_cast<std::uint32_t>(track.islands.size()));
        island_order.insert(island_order.end(), track.islands.begin(), track.islands.end());
    }
    }

    auto py_track_lengths = py::array_t<std::uint32_t>(track_lengths.size());
    auto py_order = py::array_t<std::uint32_t>(island_order.size());
    std::memcpy(py_track_lengths.mutable_data(), track_lengths.data(), track_lengths.size() * sizeof(std::uint32_t));
    std::memcpy(py_order.mutable_data(), island_order.data(), island_order.size() * sizeof(std::uint32_t));
    return py::make_tuple(py_track_lengths, py_order);
}

py::dict plan_track_intensity_equal_fidelity(
    py::list track_segments,
    int precision,
    int delta_ref_window,
    int padded_delta_max_diff,
    bool use_max_ref_curr,
    bool adaptive_padded_delta_accounting,
    bool enable_second_order_delta
) {
    const auto n = static_cast<std::size_t>(py::len(track_segments));
    auto int_kinds = py::array_t<std::uint8_t>(n);
    auto ref_offsets = py::array_t<std::uint8_t>(n);
    auto encoded_lens = py::array_t<std::uint32_t>(n);
    auto full_modes = py::array_t<std::uint8_t>(n);
    auto full_estimates = py::array_t<std::uint32_t>(n);

    auto* kind_ptr = int_kinds.mutable_data();
    auto* ref_ptr = ref_offsets.mutable_data();
    auto* len_ptr = encoded_lens.mutable_data();
    auto* mode_ptr = full_modes.mutable_data();
    auto* full_est_ptr = full_estimates.mutable_data();

    constexpr std::uint8_t INT_KIND_FULL_V = 0;
    constexpr std::uint8_t INT_KIND_DELTA_V = 1;
    constexpr std::uint8_t INT_KIND_DELTA2_V = 2;
    constexpr std::uint8_t FULL_MODE_NO_REF = 1;
    constexpr std::uint8_t FULL_MODE_REJECT = 2;

    std::uint32_t n_delta_islands = 0;
    std::uint32_t n_delta2_islands = 0;
    std::uint32_t n_padded_delta_islands = 0;
    std::uint32_t n_byte_accounting_rejects = 0;
    std::uint32_t n_same_length_pairs = 0;
    std::uint64_t delta_ref_offset_sum = 0;

    std::vector<py::array_t<double, py::array::c_style | py::array::forcecast>> seg_arrays;
    seg_arrays.reserve(n);
    std::vector<py::buffer_info> seg_bufs;
    seg_bufs.reserve(n);
    for (std::size_t i = 0; i < n; ++i) {
        auto arr = track_segments[i].cast<py::array_t<double, py::array::c_style | py::array::forcecast>>();
        seg_bufs.push_back(arr.request());
        seg_arrays.push_back(arr);
    }

    {
    py::gil_scoped_release release;
    for (std::size_t i = 0; i < n; ++i) {
        kind_ptr[i] = INT_KIND_FULL_V;
        ref_ptr[i] = 0;
        len_ptr[i] = 0;
        mode_ptr[i] = 0;
        const auto curr_len = static_cast<std::size_t>(seg_bufs[i].size);
        const auto* curr_ptr = static_cast<const double*>(seg_bufs[i].ptr);
        const auto full_est = static_cast<std::uint32_t>(
            estimate_scaled_full_bytes_core(curr_ptr, curr_len, static_cast<double>(precision))
        );
        full_est_ptr[i] = full_est;

        if (i == 0) {
            mode_ptr[i] = FULL_MODE_NO_REF;
            continue;
        }

        const std::size_t lookback = std::min<std::size_t>(
            static_cast<std::size_t>(delta_ref_window < 0 ? 0 : delta_ref_window),
            i
        );
        bool found = false;
        std::size_t best_offset = 0;
        std::uint32_t best_delta_est = 0;
        std::uint32_t best_encoded_len = 0;
        int best_length_diff = 0;
        double best_mean_abs = 0.0;

        for (std::size_t ref_offset = 1; ref_offset <= lookback; ++ref_offset) {
            const auto ref_idx = i - ref_offset;
            const auto ref_len = static_cast<std::size_t>(seg_bufs[ref_idx].size);
            const auto* ref_ptr_local = static_cast<const double*>(seg_bufs[ref_idx].ptr);
            const int length_diff = static_cast<int>(curr_len > ref_len ? curr_len - ref_len : ref_len - curr_len);
            if (length_diff > padded_delta_max_diff) {
                continue;
            }
            const auto encoded_len = static_cast<std::size_t>(delta_encoded_length_core(curr_len, ref_len, use_max_ref_curr));
            const auto delta_est = static_cast<std::uint32_t>(
                estimate_scaled_delta_bytes_core(
                    curr_ptr,
                    curr_len,
                    ref_ptr_local,
                    ref_len,
                    encoded_len,
                    static_cast<double>(precision)
                )
            );
            if (delta_est >= full_est) {
                continue;
            }
            const double mean_abs = mean_abs_padded_delta(curr_ptr, curr_len, ref_ptr_local, ref_len, encoded_len);
            if (
                !found
                || delta_est < best_delta_est
                || (delta_est == best_delta_est && mean_abs < best_mean_abs)
                || (delta_est == best_delta_est && mean_abs == best_mean_abs && length_diff < best_length_diff)
                || (delta_est == best_delta_est && mean_abs == best_mean_abs && length_diff == best_length_diff && ref_offset < best_offset)
            ) {
                found = true;
                best_offset = ref_offset;
                best_delta_est = delta_est;
                best_encoded_len = static_cast<std::uint32_t>(encoded_len);
                best_length_diff = length_diff;
                best_mean_abs = mean_abs;
            }
        }

        if (!found) {
            mode_ptr[i] = FULL_MODE_NO_REF;
            continue;
        }

        if (best_length_diff == 0) {
            ++n_same_length_pairs;
        } else {
            ++n_padded_delta_islands;
        }

        if (adaptive_padded_delta_accounting && best_length_diff > 0 && best_delta_est >= full_est) {
            ++n_byte_accounting_rejects;
            mode_ptr[i] = FULL_MODE_REJECT;
            continue;
        }

        if (enable_second_order_delta && i >= 2) {
            const auto prev_idx = i - 1;
            const auto prev2_idx = i - 2;
            const auto prev_len = static_cast<std::size_t>(seg_bufs[prev_idx].size);
            const auto prev2_len = static_cast<std::size_t>(seg_bufs[prev2_idx].size);
            if (prev_len == curr_len && prev2_len == curr_len) {
                const auto* prev_ptr = static_cast<const double*>(seg_bufs[prev_idx].ptr);
                const auto* prev2_ptr = static_cast<const double*>(seg_bufs[prev2_idx].ptr);
                std::vector<std::int64_t> delta2(curr_len);
                for (std::size_t j = 0; j < curr_len; ++j) {
                    const auto curr64 = round_even_to_i64(curr_ptr[j] * static_cast<double>(precision));
                    const auto prev64 = round_even_to_i64(prev_ptr[j] * static_cast<double>(precision));
                    const auto prev264 = round_even_to_i64(prev2_ptr[j] * static_cast<double>(precision));
                    delta2[j] = curr64 - (2 * prev64) + prev264;
                }
                const auto delta2_est = static_cast<std::uint32_t>(estimate_int64_main_bytes_core(delta2.data(), curr_len));
                if (delta2_est < full_est && delta2_est < best_delta_est) {
                    kind_ptr[i] = INT_KIND_DELTA2_V;
                    ++n_delta_islands;
                    ++n_delta2_islands;
                    continue;
                }
            }
        }

        kind_ptr[i] = INT_KIND_DELTA_V;
        ref_ptr[i] = static_cast<std::uint8_t>(best_offset);
        len_ptr[i] = best_encoded_len;
        ++n_delta_islands;
        delta_ref_offset_sum += best_offset;
    }
    }

    py::dict stats;
    stats["n_delta_islands"] = py::int_(n_delta_islands);
    stats["n_delta2_islands"] = py::int_(n_delta2_islands);
    stats["n_padded_delta_islands"] = py::int_(n_padded_delta_islands);
    stats["n_byte_accounting_rejects"] = py::int_(n_byte_accounting_rejects);
    stats["n_same_length_pairs"] = py::int_(n_same_length_pairs);
    stats["delta_ref_offset_sum"] = py::int_(delta_ref_offset_sum);

    py::dict out;
    out["int_kinds"] = int_kinds;
    out["delta_ref_offsets"] = ref_offsets;
    out["delta_encoded_lengths"] = encoded_lens;
    out["full_modes"] = full_modes;
    out["full_estimates"] = full_estimates;
    out["stats"] = stats;
    return out;
}

py::dict best_residual_mz_model(
    py::array_t<std::int64_t, py::array::c_style | py::array::forcecast> prev64,
    py::array_t<std::int64_t, py::array::c_style | py::array::forcecast> curr64,
    bool enhanced_residual_model
) {
    auto prev_buf = prev64.request();
    auto curr_buf = curr64.request();
    const auto count = static_cast<std::size_t>(curr_buf.size);
    if (count != static_cast<std::size_t>(prev_buf.size)) {
        throw std::runtime_error("best_residual_mz_model length mismatch");
    }
    py::dict out;
    if (count == 0) {
        out["found"] = py::bool_(false);
        return out;
    }

    const auto* prev_ptr = static_cast<const std::int64_t*>(prev_buf.ptr);
    const auto* curr_ptr = static_cast<const std::int64_t*>(curr_buf.ptr);
    std::vector<std::int64_t> diff(count);

    bool found = false;
    int best_offset = 0;
    int best_step = 0;
    std::size_t best_cost = 0;
    std::vector<std::int32_t> best_residual;
    {
    py::gil_scoped_release release;
    for (std::size_t i = 0; i < count; ++i) {
        diff[i] = curr_ptr[i] - prev_ptr[i];
    }

    std::vector<std::int64_t> sorted_diff = diff;
    std::sort(sorted_diff.begin(), sorted_diff.end());
    const auto median_i64 = sorted_diff[count / 2];
    long double sum = 0.0L;
    for (const auto v : diff) {
        sum += static_cast<long double>(v);
    }
    const auto mean_i64 = static_cast<std::int64_t>(std::nearbyint(sum / static_cast<long double>(count)));

    std::vector<int> scalar_candidates = {
        static_cast<int>(diff[0]),
        static_cast<int>(median_i64),
        static_cast<int>(mean_i64),
    };
    if (enhanced_residual_model) {
        scalar_candidates = expand_small_int_candidates_core(scalar_candidates, 1);
    }
    std::unordered_set<std::uint64_t> seen;
    seen.reserve(32);

    auto try_candidate = [&](int offset, int step) {
        if (!seen.insert(pack_int_pair_key(offset, step)).second) {
            return;
        }
        std::vector<std::int32_t> residual(count);
        for (std::size_t i = 0; i < count; ++i) {
            const std::int64_t pred = static_cast<std::int64_t>(offset) + static_cast<std::int64_t>(step) * static_cast<std::int64_t>(i);
            residual[i] = static_cast<std::int32_t>(diff[i] - pred);
        }
        const auto cost = estimate_mz_model_bytes_core(
            static_cast<std::int32_t>(offset),
            static_cast<std::int32_t>(step),
            residual.data(),
            count
        );
        if (!found || cost < best_cost) {
            found = true;
            best_offset = offset;
            best_step = step;
            best_cost = cost;
            best_residual = std::move(residual);
        }
    };

    for (const auto offset : scalar_candidates) {
        try_candidate(offset, 0);
    }

    if (enhanced_residual_model && count >= 3) {
        std::vector<std::int64_t> diff_delta(count - 1);
        for (std::size_t i = 1; i < count; ++i) {
            diff_delta[i - 1] = diff[i] - diff[i - 1];
        }
        auto sorted_delta = diff_delta;
        std::sort(sorted_delta.begin(), sorted_delta.end());
        long double delta_sum = 0.0L;
        for (const auto v : diff_delta) {
            delta_sum += static_cast<long double>(v);
        }
        std::vector<int> step_candidates = {
            static_cast<int>(std::nearbyint((static_cast<long double>(diff.back()) - static_cast<long double>(diff.front())) / static_cast<long double>(count - 1))),
            static_cast<int>(sorted_delta[diff_delta.size() / 2]),
            static_cast<int>(std::nearbyint(delta_sum / static_cast<long double>(diff_delta.size()))),
        };
        step_candidates = expand_small_int_candidates_core(step_candidates, 1);
        for (const auto step : step_candidates) {
            std::vector<std::int64_t> base_vals(count);
            for (std::size_t i = 0; i < count; ++i) {
                base_vals[i] = diff[i] - static_cast<std::int64_t>(step) * static_cast<std::int64_t>(i);
            }
            auto sorted_base = base_vals;
            std::sort(sorted_base.begin(), sorted_base.end());
            long double base_sum = 0.0L;
            for (const auto v : base_vals) {
                base_sum += static_cast<long double>(v);
            }
            std::vector<int> base_candidates = {
                static_cast<int>(std::nearbyint(base_sum / static_cast<long double>(count))),
                static_cast<int>(sorted_base[count / 2]),
                static_cast<int>(diff[0]),
            };
            base_candidates = expand_small_int_candidates_core(base_candidates, 1);
            for (const auto base_offset : base_candidates) {
                try_candidate(base_offset, step);
            }
        }
    }
    }

    out["found"] = py::bool_(found);
    if (!found) {
        return out;
    }
    auto residual_arr = py::array_t<std::int32_t>(best_residual.size());
    std::memcpy(residual_arr.mutable_data(), best_residual.data(), best_residual.size() * sizeof(std::int32_t));
    out["offset"] = py::int_(best_offset);
    out["step"] = py::int_(best_step);
    out["cost"] = py::int_(best_cost);
    out["residual"] = residual_arr;
    return out;
}

py::dict select_mz_representation_u64(
    py::array_t<std::uint64_t, py::array::c_style | py::array::forcecast> prev_q,
    py::array_t<std::uint64_t, py::array::c_style | py::array::forcecast> curr_q,
    bool allow_residual_model,
    bool enhanced_residual_model
) {
    auto prev_buf = prev_q.request();
    auto curr_buf = curr_q.request();
    const auto count = static_cast<std::size_t>(curr_buf.size);
    if (count != static_cast<std::size_t>(prev_buf.size)) {
        throw std::runtime_error("select_mz_representation_u64 length mismatch");
    }
    py::dict out;
    out["kind"] = py::int_(0);
    if (count == 0) {
        return out;
    }

    const auto* prev_ptr = static_cast<const std::uint64_t*>(prev_buf.ptr);
    const auto* curr_ptr = static_cast<const std::uint64_t*>(curr_buf.ptr);

    bool exact_offset = true;
    std::int64_t exact_offset_value = 0;
    bool found = false;
    int best_offset = 0;
    int best_step = 0;
    std::size_t best_cost = 0;
    std::vector<std::int32_t> best_residual;

    {
    py::gil_scoped_release release;

    exact_offset_value = static_cast<std::int64_t>(curr_ptr[0]) - static_cast<std::int64_t>(prev_ptr[0]);
    for (std::size_t i = 0; i < count; ++i) {
        const std::int64_t delta = static_cast<std::int64_t>(curr_ptr[i]) - static_cast<std::int64_t>(prev_ptr[i]);
        if (delta != exact_offset_value) {
            exact_offset = false;
            break;
        }
    }
    if (exact_offset) {
        if (exact_offset_value < static_cast<std::int64_t>(INT32_MIN) || exact_offset_value > static_cast<std::int64_t>(INT32_MAX)) {
            throw std::runtime_error("select_mz_representation_u64: exact offset exceeds int32 range");
        }
    } else if (allow_residual_model) {
        const std::size_t raw_cost = estimate_raw_mz_local_bytes_u64_core(curr_ptr, count);
        std::vector<std::int64_t> diff(count);
        for (std::size_t i = 0; i < count; ++i) {
            diff[i] = static_cast<std::int64_t>(curr_ptr[i]) - static_cast<std::int64_t>(prev_ptr[i]);
        }

        std::vector<std::int64_t> sorted_diff = diff;
        std::sort(sorted_diff.begin(), sorted_diff.end());
        const auto median_i64 = sorted_diff[count / 2];
        long double sum = 0.0L;
        for (const auto v : diff) {
            sum += static_cast<long double>(v);
        }
        const auto mean_i64 = static_cast<std::int64_t>(std::nearbyint(sum / static_cast<long double>(count)));

        auto append_if_int32 = [](std::vector<int>& out, std::int64_t value) {
            if (value < static_cast<std::int64_t>(INT32_MIN) || value > static_cast<std::int64_t>(INT32_MAX)) {
                return;
            }
            out.push_back(static_cast<int>(value));
        };

        std::vector<int> scalar_candidates;
        scalar_candidates.reserve(3);
        append_if_int32(scalar_candidates, diff[0]);
        append_if_int32(scalar_candidates, median_i64);
        append_if_int32(scalar_candidates, mean_i64);
        if (enhanced_residual_model) {
            scalar_candidates = expand_small_int_candidates_core(scalar_candidates, 1);
        }
        std::unordered_set<std::uint64_t> seen;
        seen.reserve(32);

        auto try_candidate = [&](int offset, int step) {
            if (!seen.insert(pack_int_pair_key(offset, step)).second) {
                return;
            }
            std::vector<std::int32_t> residual(count);
            for (std::size_t i = 0; i < count; ++i) {
                const std::int64_t pred = static_cast<std::int64_t>(offset) + static_cast<std::int64_t>(step) * static_cast<std::int64_t>(i);
                const std::int64_t residual_i64 = diff[i] - pred;
                if (residual_i64 < static_cast<std::int64_t>(INT32_MIN) || residual_i64 > static_cast<std::int64_t>(INT32_MAX)) {
                    return;
                }
                residual[i] = static_cast<std::int32_t>(residual_i64);
            }
            const auto cost = estimate_mz_model_bytes_core(
                static_cast<std::int32_t>(offset),
                static_cast<std::int32_t>(step),
                residual.data(),
                count
            );
            if (cost >= raw_cost) {
                return;
            }
            if (!found || cost < best_cost) {
                found = true;
                best_offset = offset;
                best_step = step;
                best_cost = cost;
                best_residual = std::move(residual);
            }
        };

        for (const auto offset : scalar_candidates) {
            try_candidate(offset, 0);
        }

        if (enhanced_residual_model && count >= 3) {
            std::vector<std::int64_t> diff_delta(count - 1);
            for (std::size_t i = 1; i < count; ++i) {
                diff_delta[i - 1] = diff[i] - diff[i - 1];
            }
            auto sorted_delta = diff_delta;
            std::sort(sorted_delta.begin(), sorted_delta.end());
            long double delta_sum = 0.0L;
            for (const auto v : diff_delta) {
                delta_sum += static_cast<long double>(v);
            }
            std::vector<int> step_candidates;
            step_candidates.reserve(3);
            append_if_int32(
                step_candidates,
                static_cast<std::int64_t>(std::nearbyint((static_cast<long double>(diff.back()) - static_cast<long double>(diff.front())) / static_cast<long double>(count - 1)))
            );
            append_if_int32(step_candidates, sorted_delta[diff_delta.size() / 2]);
            append_if_int32(
                step_candidates,
                static_cast<std::int64_t>(std::nearbyint(delta_sum / static_cast<long double>(diff_delta.size())))
            );
            step_candidates = expand_small_int_candidates_core(step_candidates, 1);
            for (const auto step : step_candidates) {
                std::vector<std::int64_t> base_vals(count);
                for (std::size_t i = 0; i < count; ++i) {
                    base_vals[i] = diff[i] - static_cast<std::int64_t>(step) * static_cast<std::int64_t>(i);
                }
                auto sorted_base = base_vals;
                std::sort(sorted_base.begin(), sorted_base.end());
                long double base_sum = 0.0L;
                for (const auto v : base_vals) {
                    base_sum += static_cast<long double>(v);
                }
                std::vector<int> base_candidates;
                base_candidates.reserve(3);
                append_if_int32(
                    base_candidates,
                    static_cast<std::int64_t>(std::nearbyint(base_sum / static_cast<long double>(count)))
                );
                append_if_int32(base_candidates, sorted_base[count / 2]);
                append_if_int32(base_candidates, diff[0]);
                base_candidates = expand_small_int_candidates_core(base_candidates, 1);
                for (const auto base_offset : base_candidates) {
                    try_candidate(base_offset, step);
                }
            }
        }
    }
    }

    if (exact_offset) {
        out["kind"] = py::int_(1);
        out["offset"] = py::int_(exact_offset_value);
        return out;
    }
    if (!found) {
        return out;
    }
    auto residual_arr = py::array_t<std::int32_t>(best_residual.size());
    std::memcpy(residual_arr.mutable_data(), best_residual.data(), best_residual.size() * sizeof(std::int32_t));
    out["kind"] = py::int_(2);
    out["offset"] = py::int_(best_offset);
    out["step"] = py::int_(best_step);
    out["cost"] = py::int_(best_cost);
    out["residual"] = residual_arr;
    return out;
}

py::dict build_representation_compact_views(
    py::array_t<double, py::array::c_style | py::array::forcecast> mz_data,
    py::array_t<double, py::array::c_style | py::array::forcecast> intensity_data,
    py::array_t<std::uint64_t, py::array::c_style | py::array::forcecast> scan_base_offsets,
    py::array_t<std::uint32_t, py::array::c_style | py::array::forcecast> track_offsets,
    py::array_t<std::uint32_t, py::array::c_style | py::array::forcecast> scan_indices,
    py::array_t<std::uint32_t, py::array::c_style | py::array::forcecast> array_start_indices,
    py::array_t<std::uint32_t, py::array::c_style | py::array::forcecast> n_points,
    bool omit_island_mz,
    int mz_precision,
    bool use_local_mz_refs,
    bool allow_mz_model,
    bool allow_mz_residual_model,
    bool enhanced_residual_model,
    bool eq_mode,
    int eq_precision,
    int delta_ref_window,
    int padded_delta_max_diff,
    bool use_max_ref_curr,
    bool adaptive_padded_delta_accounting,
    bool enable_second_order_delta,
    bool enable_cross_track_full_prediction,
    int cross_track_candidate_limit,
    int cross_track_min_gain_bytes,
    bool enable_byteaware_delta_ref_selection
) {
    auto mz_buf = mz_data.request();
    auto int_buf = intensity_data.request();
    auto base_buf = scan_base_offsets.request();
    auto track_buf = track_offsets.request();
    auto scan_buf = scan_indices.request();
    auto start_buf = array_start_indices.request();
    auto npts_buf = n_points.request();

    const auto island_count = static_cast<std::size_t>(scan_buf.size);
    if (island_count != static_cast<std::size_t>(start_buf.size) || island_count != static_cast<std::size_t>(npts_buf.size)) {
        throw std::runtime_error("build_representation_compact_views island metadata length mismatch");
    }
    if (mz_buf.size != int_buf.size) {
        throw std::runtime_error("build_representation_compact_views flat mz/intensity length mismatch");
    }
    const auto track_count = static_cast<std::size_t>(track_buf.size == 0 ? 0 : track_buf.size - 1);
    if (track_count == 0 && island_count != 0) {
        throw std::runtime_error("build_representation_compact_views empty track_offsets with nonzero islands");
    }

    const auto* mz_ptr = static_cast<const double*>(mz_buf.ptr);
    const auto* int_ptr = static_cast<const double*>(int_buf.ptr);
    const auto* base_ptr = static_cast<const std::uint64_t*>(base_buf.ptr);
    const auto* track_ptr = static_cast<const std::uint32_t*>(track_buf.ptr);
    const auto* scan_idx_ptr = static_cast<const std::uint32_t*>(scan_buf.ptr);
    const auto* start_idx_ptr = static_cast<const std::uint32_t*>(start_buf.ptr);
    const auto* npts_ptr = static_cast<const std::uint32_t*>(npts_buf.ptr);

    constexpr std::uint8_t MZ_KIND_RAW_V = 0;
    constexpr std::uint8_t MZ_KIND_PREV_OFFSET_V = 1;
    constexpr std::uint8_t MZ_KIND_PREV_OFFSET_RESIDUAL_V = 2;
    constexpr std::uint8_t INT_KIND_FULL_V = 0;
    constexpr std::uint8_t INT_KIND_DELTA_V = 1;
    constexpr std::uint8_t INT_KIND_DELTA2_V = 2;

    double mz_scale = 1e6;
    if (mz_precision == 4) {
        mz_scale = 1e4;
    } else if (mz_precision == 5) {
        mz_scale = 1e5;
    } else if (mz_precision == 6) {
        mz_scale = 1e6;
    } else {
        throw std::runtime_error("build_representation_compact_views: unsupported mz_precision");
    }

    auto segment_ptr = [&](std::size_t island_idx) -> const double* {
        return int_ptr + static_cast<std::size_t>(base_ptr[scan_idx_ptr[island_idx]]) + static_cast<std::size_t>(start_idx_ptr[island_idx]);
    };
    auto segment_len = [&](std::size_t island_idx) -> std::size_t {
        return static_cast<std::size_t>(npts_ptr[island_idx]);
    };
    auto mz_segment_ptr = [&](std::size_t island_idx) -> const double* {
        return mz_ptr + static_cast<std::size_t>(base_ptr[scan_idx_ptr[island_idx]]) + static_cast<std::size_t>(start_idx_ptr[island_idx]);
    };

    std::vector<std::uint8_t> mz_kinds(omit_island_mz ? 0 : island_count, MZ_KIND_RAW_V);
    std::vector<std::uint32_t> mz_ref_indices(omit_island_mz ? 0 : island_count, 0);
    std::vector<std::uint8_t> int_kinds(island_count, INT_KIND_FULL_V);
    std::vector<std::uint8_t> delta_ref_offsets;
    std::vector<std::uint32_t> cross_track_ref_indices;

    std::vector<std::uint32_t> mz_library_lengths;
    std::vector<std::uint64_t> mz_library_first_values;
    std::vector<std::uint32_t> mz_library_delta_values;
    std::vector<std::int32_t> mz_model_offsets;
    std::vector<std::int32_t> mz_model_steps;
    std::vector<std::uint32_t> mz_model_residual_lengths;
    std::vector<std::int32_t> mz_residual_concat;

    std::uint64_t n_delta_islands = 0;
    std::uint64_t n_delta2_islands = 0;
    std::uint64_t n_padded_delta_islands = 0;
    std::uint64_t n_byte_accounting_rejects = 0;
    std::uint64_t n_same_length_pairs = 0;
    std::uint64_t delta_ref_offset_sum = 0;
    std::uint64_t n_cross_track_delta_islands = 0;
    std::uint64_t n_offset_arrays = 0;
    std::uint64_t n_residual_offset_arrays = 0;
    std::uint64_t n_affine_residual_arrays = 0;
    std::uint64_t total_mz_points = 0;
    std::uint64_t offset_mz_points = 0;
    std::uint64_t residual_mz_points = 0;
    std::uint64_t n_singleton_track_fast_path = 0;

    std::vector<std::unordered_map<std::uint32_t, std::deque<std::uint32_t>>> cross_track_history(base_buf.size);

    {
        py::gil_scoped_release release;
        std::uint64_t singleton_raw_delta_capacity = 0;
        std::uint64_t singleton_track_count = 0;
        if (!omit_island_mz && !enable_cross_track_full_prediction) {
            for (std::size_t track_idx = 0; track_idx < track_count; ++track_idx) {
                const auto track_start = static_cast<std::size_t>(track_ptr[track_idx]);
                const auto track_end = static_cast<std::size_t>(track_ptr[track_idx + 1]);
                if (track_end == track_start + 1) {
                    const auto n = segment_len(track_start);
                    ++singleton_track_count;
                    if (n > 0) {
                        singleton_raw_delta_capacity += static_cast<std::uint64_t>(n - 1);
                    }
                }
            }
            if (singleton_track_count > 0) {
                mz_library_lengths.reserve(
                    std::min<std::uint64_t>(
                        static_cast<std::uint64_t>(island_count),
                        singleton_track_count + static_cast<std::uint64_t>(1024)
                    )
                );
                mz_library_first_values.reserve(
                    std::min<std::uint64_t>(
                        static_cast<std::uint64_t>(island_count),
                        singleton_track_count + static_cast<std::uint64_t>(1024)
                    )
                );
                mz_library_delta_values.reserve(static_cast<std::size_t>(singleton_raw_delta_capacity));
            }
        }

        auto store_raw_mz = [&](const std::vector<std::uint64_t>& curr_q, std::unordered_map<std::string, std::uint32_t>* local_map) -> std::uint32_t {
            if (local_map != nullptr) {
                const std::string key(reinterpret_cast<const char*>(curr_q.data()), curr_q.size() * sizeof(std::uint64_t));
                auto it = local_map->find(key);
                if (it != local_map->end()) {
                    return it->second;
                }
                const std::uint32_t ref = static_cast<std::uint32_t>(mz_library_lengths.size());
                (*local_map)[key] = ref;
                mz_library_lengths.push_back(static_cast<std::uint32_t>(curr_q.size()));
                if (!curr_q.empty()) {
                    mz_library_first_values.push_back(curr_q[0]);
                    for (std::size_t i = 1; i < curr_q.size(); ++i) {
                        if (curr_q[i] < curr_q[i - 1]) {
                            throw std::runtime_error("build_representation_compact_views: quantized m/z must be nondecreasing");
                        }
                        const auto delta = curr_q[i] - curr_q[i - 1];
                        if (delta > UINT32_MAX) {
                            throw std::runtime_error("build_representation_compact_views: raw m/z delta exceeds uint32 range");
                        }
                        mz_library_delta_values.push_back(static_cast<std::uint32_t>(delta));
                    }
                }
                return ref;
            }
            const std::uint32_t ref = static_cast<std::uint32_t>(mz_library_lengths.size());
            mz_library_lengths.push_back(static_cast<std::uint32_t>(curr_q.size()));
            if (!curr_q.empty()) {
                mz_library_first_values.push_back(curr_q[0]);
                for (std::size_t i = 1; i < curr_q.size(); ++i) {
                    if (curr_q[i] < curr_q[i - 1]) {
                        throw std::runtime_error("build_representation_compact_views: quantized m/z must be nondecreasing");
                    }
                    const auto delta = curr_q[i] - curr_q[i - 1];
                    if (delta > UINT32_MAX) {
                        throw std::runtime_error("build_representation_compact_views: raw m/z delta exceeds uint32 range");
                    }
                    mz_library_delta_values.push_back(static_cast<std::uint32_t>(delta));
                }
            }
            return ref;
        };

        auto store_raw_mz_from_double = [&](const double* curr_mz, std::size_t curr_len) -> std::uint32_t {
            const std::uint32_t ref = static_cast<std::uint32_t>(mz_library_lengths.size());
            mz_library_lengths.push_back(static_cast<std::uint32_t>(curr_len));
            if (curr_len == 0) {
                return ref;
            }
            const auto first_q_i64 = round_even_to_i64(curr_mz[0] * mz_scale);
            if (first_q_i64 < 0 || static_cast<std::uint64_t>(first_q_i64) > INT64_MAX) {
                throw std::runtime_error("build_representation_compact_views: quantized m/z exceeds int64 range");
            }
            std::uint64_t prev_q = static_cast<std::uint64_t>(first_q_i64);
            mz_library_first_values.push_back(prev_q);
            for (std::size_t i = 1; i < curr_len; ++i) {
                const auto q_i64 = round_even_to_i64(curr_mz[i] * mz_scale);
                if (q_i64 < 0 || static_cast<std::uint64_t>(q_i64) > INT64_MAX) {
                    throw std::runtime_error("build_representation_compact_views: quantized m/z exceeds int64 range");
                }
                const auto q = static_cast<std::uint64_t>(q_i64);
                if (q < prev_q) {
                    throw std::runtime_error("build_representation_compact_views: quantized m/z must be nondecreasing");
                }
                const auto delta = q - prev_q;
                if (delta > UINT32_MAX) {
                    throw std::runtime_error("build_representation_compact_views: raw m/z delta exceeds uint32 range");
                }
                mz_library_delta_values.push_back(static_cast<std::uint32_t>(delta));
                prev_q = q;
            }
            return ref;
        };

        const bool allow_cross_track_global = eq_mode && enable_cross_track_full_prediction && eq_precision > 0;

        for (std::size_t track_idx = 0; track_idx < track_count; ++track_idx) {
            const auto track_start = static_cast<std::size_t>(track_ptr[track_idx]);
            const auto track_end = static_cast<std::size_t>(track_ptr[track_idx + 1]);
            if (track_end == track_start + 1 && !allow_cross_track_global) {
                const auto island_idx = track_start;
                const auto curr_len = segment_len(island_idx);
                if (!omit_island_mz) {
                    total_mz_points += curr_len;
                    mz_ref_indices[island_idx] = store_raw_mz_from_double(mz_segment_ptr(island_idx), curr_len);
                }
                ++n_singleton_track_fast_path;
                continue;
            }
            std::unordered_map<std::string, std::uint32_t> local_mz_refs;
            std::unordered_map<std::string, std::uint32_t>* local_mz_ref_ptr =
                (use_local_mz_refs && track_end > track_start + 1) ? &local_mz_refs : nullptr;

            std::vector<std::uint32_t> history_indices;
            history_indices.reserve(static_cast<std::size_t>(std::max(delta_ref_window, enable_second_order_delta ? 2 : 1)));
            std::vector<std::uint64_t> prev_q;

            for (std::size_t island_idx = track_start; island_idx < track_end; ++island_idx) {
                const auto curr_len = segment_len(island_idx);
                const auto* curr_intensity = segment_ptr(island_idx);

                std::vector<std::uint64_t> curr_q;
                if (!omit_island_mz) {
                    total_mz_points += curr_len;
                    const auto* curr_mz = mz_segment_ptr(island_idx);
                    curr_q.resize(curr_len);
                    for (std::size_t i = 0; i < curr_len; ++i) {
                        const auto q = round_even_to_i64(curr_mz[i] * mz_scale);
                        if (q < 0 || static_cast<std::uint64_t>(q) > INT64_MAX) {
                            throw std::runtime_error("build_representation_compact_views: quantized m/z exceeds int64 range");
                        }
                        curr_q[i] = static_cast<std::uint64_t>(q);
                    }

                    bool used_exact_offset = false;
                    bool used_residual_offset = false;
                    bool used_affine_residual = false;
                    std::uint32_t mz_ref_idx = 0;
                    std::uint8_t mz_kind = MZ_KIND_RAW_V;

                    if (prev_q.empty() || !allow_mz_model || prev_q.size() != curr_q.size()) {
                        mz_ref_idx = store_raw_mz(curr_q, local_mz_ref_ptr);
                    } else {
                        bool exact_offset = true;
                        const auto exact_offset_value = static_cast<std::int64_t>(curr_q[0]) - static_cast<std::int64_t>(prev_q[0]);
                        for (std::size_t i = 0; i < curr_q.size(); ++i) {
                            const auto delta = static_cast<std::int64_t>(curr_q[i]) - static_cast<std::int64_t>(prev_q[i]);
                            if (delta != exact_offset_value) {
                                exact_offset = false;
                                break;
                            }
                        }
                        if (exact_offset) {
                            if (exact_offset_value < static_cast<std::int64_t>(INT32_MIN) || exact_offset_value > static_cast<std::int64_t>(INT32_MAX)) {
                                throw std::runtime_error("build_representation_compact_views: exact offset exceeds int32 range");
                            }
                            mz_kind = MZ_KIND_PREV_OFFSET_V;
                            mz_ref_idx = static_cast<std::uint32_t>(mz_model_offsets.size());
                            mz_model_offsets.push_back(static_cast<std::int32_t>(exact_offset_value));
                            mz_model_steps.push_back(0);
                            mz_model_residual_lengths.push_back(0);
                            used_exact_offset = true;
                        } else if (allow_mz_residual_model) {
                            const auto raw_cost = estimate_raw_mz_local_bytes_u64_core(curr_q.data(), curr_q.size());
                            std::vector<std::int64_t> diff(curr_q.size());
                            for (std::size_t i = 0; i < curr_q.size(); ++i) {
                                diff[i] = static_cast<std::int64_t>(curr_q[i]) - static_cast<std::int64_t>(prev_q[i]);
                            }
                            std::vector<std::int64_t> sorted_diff = diff;
                            std::sort(sorted_diff.begin(), sorted_diff.end());
                            const auto median_i64 = sorted_diff[diff.size() / 2];
                            long double diff_sum = 0.0L;
                            for (const auto v : diff) {
                                diff_sum += static_cast<long double>(v);
                            }
                            const auto mean_i64 = static_cast<std::int64_t>(std::nearbyint(diff_sum / static_cast<long double>(diff.size())));
                            auto append_if_int32 = [](std::vector<int>& out, std::int64_t value) {
                                if (value < static_cast<std::int64_t>(INT32_MIN) || value > static_cast<std::int64_t>(INT32_MAX)) {
                                    return;
                                }
                                out.push_back(static_cast<int>(value));
                            };
                            std::vector<int> scalar_candidates;
                            scalar_candidates.reserve(3);
                            append_if_int32(scalar_candidates, diff[0]);
                            append_if_int32(scalar_candidates, median_i64);
                            append_if_int32(scalar_candidates, mean_i64);
                            if (enhanced_residual_model) {
                                scalar_candidates = expand_small_int_candidates_core(scalar_candidates, 1);
                            }
                            bool found = false;
                            int best_offset = 0;
                            int best_step = 0;
                            std::size_t best_cost = 0;
                            std::vector<std::int32_t> best_residual;
                            std::unordered_set<std::uint64_t> seen;
                            seen.reserve(32);
                            auto try_candidate = [&](int offset, int step) {
                                if (!seen.insert(pack_int_pair_key(offset, step)).second) {
                                    return;
                                }
                                std::vector<std::int32_t> residual(curr_q.size());
                                for (std::size_t i = 0; i < curr_q.size(); ++i) {
                                    const auto pred = static_cast<std::int64_t>(offset) + static_cast<std::int64_t>(step) * static_cast<std::int64_t>(i);
                                    const auto residual_i64 = diff[i] - pred;
                                    if (residual_i64 < static_cast<std::int64_t>(INT32_MIN) || residual_i64 > static_cast<std::int64_t>(INT32_MAX)) {
                                        return;
                                    }
                                    residual[i] = static_cast<std::int32_t>(residual_i64);
                                }
                                const auto cost = estimate_mz_model_bytes_core(static_cast<std::int32_t>(offset), static_cast<std::int32_t>(step), residual.data(), residual.size());
                                if (cost >= raw_cost) {
                                    return;
                                }
                                if (!found || cost < best_cost) {
                                    found = true;
                                    best_offset = offset;
                                    best_step = step;
                                    best_cost = cost;
                                    best_residual = std::move(residual);
                                }
                            };
                            for (const auto offset : scalar_candidates) {
                                try_candidate(offset, 0);
                            }
                            if (enhanced_residual_model && curr_q.size() >= 3) {
                                std::vector<std::int64_t> diff_delta(curr_q.size() - 1);
                                for (std::size_t i = 1; i < curr_q.size(); ++i) {
                                    diff_delta[i - 1] = diff[i] - diff[i - 1];
                                }
                                auto sorted_delta = diff_delta;
                                std::sort(sorted_delta.begin(), sorted_delta.end());
                                long double delta_sum = 0.0L;
                                for (const auto v : diff_delta) {
                                    delta_sum += static_cast<long double>(v);
                                }
                                std::vector<int> step_candidates;
                                step_candidates.reserve(3);
                                append_if_int32(step_candidates, static_cast<std::int64_t>(std::nearbyint((static_cast<long double>(diff.back()) - static_cast<long double>(diff.front())) / static_cast<long double>(curr_q.size() - 1))));
                                append_if_int32(step_candidates, sorted_delta[diff_delta.size() / 2]);
                                append_if_int32(step_candidates, static_cast<std::int64_t>(std::nearbyint(delta_sum / static_cast<long double>(diff_delta.size()))));
                                step_candidates = expand_small_int_candidates_core(step_candidates, 1);
                                for (const auto step : step_candidates) {
                                    std::vector<std::int64_t> base_vals(curr_q.size());
                                    for (std::size_t i = 0; i < curr_q.size(); ++i) {
                                        base_vals[i] = diff[i] - static_cast<std::int64_t>(step) * static_cast<std::int64_t>(i);
                                    }
                                    auto sorted_base = base_vals;
                                    std::sort(sorted_base.begin(), sorted_base.end());
                                    long double base_sum = 0.0L;
                                    for (const auto v : base_vals) {
                                        base_sum += static_cast<long double>(v);
                                    }
                                    std::vector<int> base_candidates;
                                    base_candidates.reserve(3);
                                    append_if_int32(base_candidates, static_cast<std::int64_t>(std::nearbyint(base_sum / static_cast<long double>(curr_q.size()))));
                                    append_if_int32(base_candidates, sorted_base[curr_q.size() / 2]);
                                    append_if_int32(base_candidates, diff[0]);
                                    base_candidates = expand_small_int_candidates_core(base_candidates, 1);
                                    for (const auto base_offset : base_candidates) {
                                        try_candidate(base_offset, step);
                                    }
                                }
                            }
                            if (found) {
                                mz_kind = MZ_KIND_PREV_OFFSET_RESIDUAL_V;
                                mz_ref_idx = static_cast<std::uint32_t>(mz_model_offsets.size());
                                mz_model_offsets.push_back(best_offset);
                                mz_model_steps.push_back(best_step);
                                mz_model_residual_lengths.push_back(static_cast<std::uint32_t>(best_residual.size()));
                                mz_residual_concat.insert(mz_residual_concat.end(), best_residual.begin(), best_residual.end());
                                used_residual_offset = true;
                                used_affine_residual = best_step != 0;
                            } else {
                                mz_ref_idx = store_raw_mz(curr_q, local_mz_ref_ptr);
                            }
                        } else {
                            mz_ref_idx = store_raw_mz(curr_q, local_mz_ref_ptr);
                        }
                    }

                    mz_kinds[island_idx] = mz_kind;
                    mz_ref_indices[island_idx] = mz_ref_idx;
                    if (used_exact_offset) {
                        ++n_offset_arrays;
                        offset_mz_points += curr_len;
                    }
                    if (used_residual_offset) {
                        ++n_residual_offset_arrays;
                        residual_mz_points += curr_len;
                    }
                    if (used_affine_residual) {
                        ++n_affine_residual_arrays;
                    }
                }

                std::uint8_t int_kind = INT_KIND_FULL_V;
                std::size_t chosen_ref_offset = 0;
                std::uint32_t chosen_cross_track_ref = 0;
                bool use_cross_track_ref = false;

                const bool use_byteaware = eq_mode && enable_byteaware_delta_ref_selection && eq_precision > 0;
                const bool allow_cross_track = allow_cross_track_global;
                int selected_full_est = 0;
                int selected_delta_est = 0;
                bool has_selected_delta_est = false;

                if (eq_mode && eq_precision > 0) {
                    selected_full_est = static_cast<int>(estimate_scaled_full_bytes_core(curr_intensity, curr_len, static_cast<double>(eq_precision)));
                }

                std::size_t lookback = std::min<std::size_t>(history_indices.size(), static_cast<std::size_t>(delta_ref_window < 0 ? 0 : delta_ref_window));
                bool found_in_track_ref = false;
                std::size_t best_offset = 0;
                int best_length_diff = 0;
                double best_mean_abs = 0.0;
                int best_delta_est = 0;
                const double* best_ref_ptr = nullptr;
                std::size_t best_ref_len = 0;

                for (std::size_t ref_offset = 1; ref_offset <= lookback; ++ref_offset) {
                    const auto ref_island_idx = static_cast<std::size_t>(history_indices[history_indices.size() - ref_offset]);
                    const auto ref_len = segment_len(ref_island_idx);
                    const auto* ref_ptr_local = segment_ptr(ref_island_idx);
                    const int length_diff = static_cast<int>(curr_len > ref_len ? curr_len - ref_len : ref_len - curr_len);
                    if (length_diff > padded_delta_max_diff) {
                        continue;
                    }
                    const auto encoded_len = static_cast<std::size_t>(delta_encoded_length_core(curr_len, ref_len, use_max_ref_curr));
                    if (use_byteaware) {
                        const auto delta_est = static_cast<int>(estimate_scaled_delta_bytes_core(curr_intensity, curr_len, ref_ptr_local, ref_len, encoded_len, static_cast<double>(eq_precision)));
                        if (delta_est >= selected_full_est) {
                            continue;
                        }
                        const double mean_abs = mean_abs_padded_delta(curr_intensity, curr_len, ref_ptr_local, ref_len, encoded_len);
                        if (
                            !found_in_track_ref
                            || delta_est < best_delta_est
                            || (delta_est == best_delta_est && mean_abs < best_mean_abs)
                            || (delta_est == best_delta_est && mean_abs == best_mean_abs && length_diff < best_length_diff)
                            || (delta_est == best_delta_est && mean_abs == best_mean_abs && length_diff == best_length_diff && ref_offset < best_offset)
                        ) {
                            found_in_track_ref = true;
                            best_offset = ref_offset;
                            best_length_diff = length_diff;
                            best_mean_abs = mean_abs;
                            best_delta_est = delta_est;
                            best_ref_ptr = ref_ptr_local;
                            best_ref_len = ref_len;
                        }
                    } else {
                        const double mean_abs = mean_abs_padded_delta(curr_intensity, curr_len, ref_ptr_local, ref_len, encoded_len);
                        if (
                            !found_in_track_ref
                            || mean_abs < best_mean_abs
                            || (mean_abs == best_mean_abs && length_diff < best_length_diff)
                            || (mean_abs == best_mean_abs && length_diff == best_length_diff && ref_offset < best_offset)
                        ) {
                            found_in_track_ref = true;
                            best_offset = ref_offset;
                            best_length_diff = length_diff;
                            best_mean_abs = mean_abs;
                            best_ref_ptr = ref_ptr_local;
                            best_ref_len = ref_len;
                            best_delta_est = 0;
                        }
                    }
                }

                if (found_in_track_ref) {
                    chosen_ref_offset = best_offset;
                    int_kind = INT_KIND_DELTA_V;
                    if (use_byteaware) {
                        selected_delta_est = best_delta_est;
                        has_selected_delta_est = true;
                    }
                } else if (allow_cross_track) {
                    std::vector<std::tuple<int, std::uint32_t>> candidate_meta;
                    for (int seg_len = static_cast<int>(curr_len) - padded_delta_max_diff; seg_len <= static_cast<int>(curr_len) + padded_delta_max_diff; ++seg_len) {
                        if (seg_len < 0) {
                            continue;
                        }
                        const auto& per_scan = cross_track_history[scan_idx_ptr[island_idx]];
                        auto it = per_scan.find(static_cast<std::uint32_t>(seg_len));
                        if (it == per_scan.end()) {
                            continue;
                        }
                        for (const auto ref_idx : it->second) {
                            candidate_meta.emplace_back(std::abs(seg_len - static_cast<int>(curr_len)), ref_idx);
                        }
                    }
                    if (!candidate_meta.empty()) {
                        if (cross_track_candidate_limit > 0 && candidate_meta.size() > static_cast<std::size_t>(cross_track_candidate_limit)) {
                            std::sort(candidate_meta.begin(), candidate_meta.end(), [](const auto& a, const auto& b) {
                                if (std::get<0>(a) != std::get<0>(b)) {
                                    return std::get<0>(a) < std::get<0>(b);
                                }
                                return std::get<1>(a) > std::get<1>(b);
                            });
                            candidate_meta.resize(static_cast<std::size_t>(cross_track_candidate_limit));
                        }
                        bool found_cross = false;
                        int best_cross_len_diff = 0;
                        int best_cross_delta_est = 0;
                        std::uint32_t best_cross_ref = 0;
                        const double* best_cross_ptr = nullptr;
                        std::size_t best_cross_len = 0;
                        for (const auto& item : candidate_meta) {
                            const auto len_diff = std::get<0>(item);
                            const auto ref_idx = static_cast<std::size_t>(std::get<1>(item));
                            const auto ref_len = segment_len(ref_idx);
                            const auto* ref_ptr_local = segment_ptr(ref_idx);
                            const auto encoded_len = static_cast<std::size_t>(delta_encoded_length_core(curr_len, ref_len, use_max_ref_curr));
                            const auto delta_est = static_cast<int>(estimate_scaled_delta_bytes_core(curr_intensity, curr_len, ref_ptr_local, ref_len, encoded_len, static_cast<double>(eq_precision)));
                            if (delta_est + cross_track_min_gain_bytes >= selected_full_est) {
                                continue;
                            }
                            if (
                                !found_cross
                                || delta_est < best_cross_delta_est
                                || (delta_est == best_cross_delta_est && len_diff < best_cross_len_diff)
                                || (delta_est == best_cross_delta_est && len_diff == best_cross_len_diff && ref_idx > best_cross_ref)
                            ) {
                                found_cross = true;
                                best_cross_len_diff = len_diff;
                                best_cross_delta_est = delta_est;
                                best_cross_ref = static_cast<std::uint32_t>(ref_idx);
                                best_cross_ptr = ref_ptr_local;
                                best_cross_len = ref_len;
                            }
                        }
                        if (found_cross) {
                            chosen_ref_offset = 0;
                            chosen_cross_track_ref = best_cross_ref;
                            use_cross_track_ref = true;
                            int_kind = INT_KIND_DELTA_V;
                            selected_delta_est = best_cross_delta_est;
                            has_selected_delta_est = true;
                            best_ref_ptr = best_cross_ptr;
                            best_ref_len = best_cross_len;
                            best_length_diff = best_cross_len_diff;
                        }
                    }
                }

                if (int_kind == INT_KIND_DELTA_V && eq_mode && eq_precision > 0 && adaptive_padded_delta_accounting && best_ref_ptr != nullptr) {
                    const bool is_padded_delta = best_ref_len != curr_len;
                    if (is_padded_delta) {
                        if (!has_selected_delta_est) {
                            const auto encoded_len = static_cast<std::size_t>(delta_encoded_length_core(curr_len, best_ref_len, use_max_ref_curr));
                            selected_delta_est = static_cast<int>(estimate_scaled_delta_bytes_core(curr_intensity, curr_len, best_ref_ptr, best_ref_len, encoded_len, static_cast<double>(eq_precision)));
                            has_selected_delta_est = true;
                        }
                        if (selected_delta_est >= selected_full_est) {
                            ++n_byte_accounting_rejects;
                            int_kind = INT_KIND_FULL_V;
                            chosen_ref_offset = 0;
                            use_cross_track_ref = false;
                        }
                    }
                }

                if (int_kind == INT_KIND_DELTA_V && eq_mode && enable_second_order_delta && eq_precision > 0 && history_indices.size() >= 2) {
                    const auto prev1_idx = static_cast<std::size_t>(history_indices[history_indices.size() - 1]);
                    const auto prev2_idx = static_cast<std::size_t>(history_indices[history_indices.size() - 2]);
                    const auto prev1_len = segment_len(prev1_idx);
                    const auto prev2_len = segment_len(prev2_idx);
                    if (prev1_len == curr_len && prev2_len == curr_len) {
                        const auto* prev1_ptr = segment_ptr(prev1_idx);
                        const auto* prev2_ptr = segment_ptr(prev2_idx);
                        std::vector<std::int64_t> delta2(curr_len);
                        for (std::size_t i = 0; i < curr_len; ++i) {
                            const auto curr64 = round_even_to_i64(curr_intensity[i] * static_cast<double>(eq_precision));
                            const auto prev64 = round_even_to_i64(prev1_ptr[i] * static_cast<double>(eq_precision));
                            const auto prev264 = round_even_to_i64(prev2_ptr[i] * static_cast<double>(eq_precision));
                            delta2[i] = curr64 - (2 * prev64) + prev264;
                        }
                        const auto delta2_est = static_cast<int>(estimate_int64_main_bytes_core(delta2.data(), curr_len));
                        int compare_delta_est = has_selected_delta_est ? selected_delta_est : std::numeric_limits<int>::max();
                        if (delta2_est < std::min(selected_full_est, compare_delta_est)) {
                            int_kind = INT_KIND_DELTA2_V;
                            chosen_ref_offset = 0;
                            use_cross_track_ref = false;
                            ++n_delta2_islands;
                        }
                    }
                }

                int_kinds[island_idx] = int_kind;
                if (int_kind == INT_KIND_DELTA_V) {
                    const bool is_padded_delta = best_ref_ptr != nullptr && best_ref_len != curr_len;
                    if (!is_padded_delta) {
                        ++n_same_length_pairs;
                    } else {
                        ++n_padded_delta_islands;
                    }
                    ++n_delta_islands;
                    if (use_cross_track_ref) {
                        ++n_cross_track_delta_islands;
                        delta_ref_offsets.push_back(0);
                        cross_track_ref_indices.push_back(chosen_cross_track_ref);
                    } else {
                        delta_ref_offsets.push_back(static_cast<std::uint8_t>(chosen_ref_offset));
                        delta_ref_offset_sum += chosen_ref_offset;
                    }
                }

                if (!omit_island_mz) {
                    prev_q.swap(curr_q);
                }
                history_indices.push_back(static_cast<std::uint32_t>(island_idx));
                const std::size_t track_history_limit = static_cast<std::size_t>(std::max(delta_ref_window, enable_second_order_delta ? 2 : 1));
                if (history_indices.size() > track_history_limit) {
                    history_indices.erase(history_indices.begin(), history_indices.begin() + static_cast<std::ptrdiff_t>(history_indices.size() - track_history_limit));
                }

                if (allow_cross_track) {
                    auto& by_len = cross_track_history[scan_idx_ptr[island_idx]];
                    auto& bucket = by_len[static_cast<std::uint32_t>(curr_len)];
                    bucket.push_back(static_cast<std::uint32_t>(island_idx));
                    if (cross_track_candidate_limit > 0 && bucket.size() > static_cast<std::size_t>(cross_track_candidate_limit)) {
                        bucket.pop_front();
                    }
                }
            }
        }
    }

    py::dict stats;
    stats["n_tracks"] = py::int_(track_count);
    stats["n_islands"] = py::int_(island_count);
    stats["n_mz_arrays"] = py::int_(mz_library_lengths.size());
    stats["n_mz_offset_arrays"] = py::int_(n_offset_arrays);
    stats["n_mz_residual_offset_arrays"] = py::int_(n_residual_offset_arrays);
    stats["n_mz_affine_residual_arrays"] = py::int_(n_affine_residual_arrays);
    stats["n_delta_islands"] = py::int_(n_delta_islands);
    stats["n_delta2_islands"] = py::int_(n_delta2_islands);
    stats["n_padded_delta_islands"] = py::int_(n_padded_delta_islands);
    stats["n_byte_accounting_rejects"] = py::int_(n_byte_accounting_rejects);
    stats["n_same_length_pairs"] = py::int_(n_same_length_pairs);
    stats["delta_ref_offset_sum"] = py::int_(delta_ref_offset_sum);
    stats["n_cross_track_delta_islands"] = py::int_(n_cross_track_delta_islands);
    stats["n_singleton_track_fast_path"] = py::int_(n_singleton_track_fast_path);
    stats["total_mz_points"] = py::int_(total_mz_points);
    stats["offset_mz_points"] = py::int_(offset_mz_points);
    stats["residual_mz_points"] = py::int_(residual_mz_points);

    py::dict out;
    out["mz_kinds"] = to_py_array_copy(mz_kinds);
    out["mz_ref_indices"] = to_py_array_copy(mz_ref_indices);
    out["int_kinds"] = to_py_array_copy(int_kinds);
    out["delta_ref_offsets"] = to_py_array_copy(delta_ref_offsets);
    out["cross_track_ref_indices"] = to_py_array_copy(cross_track_ref_indices);
    out["mz_model_offsets"] = to_py_array_copy(mz_model_offsets);
    out["mz_model_steps"] = to_py_array_copy(mz_model_steps);
    out["mz_model_residual_lengths"] = to_py_array_copy(mz_model_residual_lengths);
    out["mz_residual_concat_i32"] = to_py_array_copy(mz_residual_concat);
    out["mz_library_lengths"] = to_py_array_copy(mz_library_lengths);
    out["mz_library_first_values_u64"] = to_py_array_copy(mz_library_first_values);
    out["mz_library_delta_values_u32"] = to_py_array_copy(mz_library_delta_values);
    out["stats"] = stats;
    return out;
}

py::dict fill_equal_fidelity_intensity_streams_compact_views(
    py::array_t<double, py::array::c_style | py::array::forcecast> intensity_data,
    py::array_t<std::uint64_t, py::array::c_style | py::array::forcecast> scan_base_offsets,
    py::array_t<std::uint32_t, py::array::c_style | py::array::forcecast> track_offsets,
    py::array_t<std::uint32_t, py::array::c_style | py::array::forcecast> scan_indices,
    py::array_t<std::uint32_t, py::array::c_style | py::array::forcecast> array_start_indices,
    py::array_t<std::uint32_t, py::array::c_style | py::array::forcecast> n_points,
    py::array_t<std::uint8_t, py::array::c_style | py::array::forcecast> int_kinds,
    py::array_t<std::uint8_t, py::array::c_style | py::array::forcecast> delta_ref_offsets,
    py::array_t<std::uint32_t, py::array::c_style | py::array::forcecast> cross_track_ref_indices,
    py::array_t<std::int64_t, py::array::c_style | py::array::forcecast> full_code64,
    py::array_t<std::int64_t, py::array::c_style | py::array::forcecast> delta_code64,
    int precision,
    bool use_max_ref_curr,
    bool enable_second_order_delta,
    int delta_ref_window
) {
    auto int_buf = intensity_data.request();
    auto base_buf = scan_base_offsets.request();
    auto track_buf = track_offsets.request();
    auto scan_buf = scan_indices.request();
    auto start_buf = array_start_indices.request();
    auto npts_buf = n_points.request();
    auto kind_buf = int_kinds.request();
    auto ref_buf = delta_ref_offsets.request();
    auto cross_buf = cross_track_ref_indices.request();
    auto full_buf = full_code64.request();
    auto delta_buf = delta_code64.request();

    const auto island_count = static_cast<std::size_t>(scan_buf.size);
    if (
        island_count != static_cast<std::size_t>(start_buf.size)
        || island_count != static_cast<std::size_t>(npts_buf.size)
        || island_count != static_cast<std::size_t>(kind_buf.size)
    ) {
        throw std::runtime_error("fill_equal_fidelity_intensity_streams_compact_views island metadata length mismatch");
    }
    const auto track_count = static_cast<std::size_t>(track_buf.size == 0 ? 0 : track_buf.size - 1);
    if (track_count == 0 && island_count != 0) {
        throw std::runtime_error("fill_equal_fidelity_intensity_streams_compact_views empty track_offsets with nonzero islands");
    }

    const auto* intensity_ptr = static_cast<const double*>(int_buf.ptr);
    const auto* base_ptr = static_cast<const std::uint64_t*>(base_buf.ptr);
    const auto* track_ptr = static_cast<const std::uint32_t*>(track_buf.ptr);
    const auto* scan_idx_ptr = static_cast<const std::uint32_t*>(scan_buf.ptr);
    const auto* start_idx_ptr = static_cast<const std::uint32_t*>(start_buf.ptr);
    const auto* npts_ptr = static_cast<const std::uint32_t*>(npts_buf.ptr);
    const auto* kind_ptr = static_cast<const std::uint8_t*>(kind_buf.ptr);
    const auto* ref_ptr = static_cast<const std::uint8_t*>(ref_buf.ptr);
    const auto* cross_ptr = static_cast<const std::uint32_t*>(cross_buf.ptr);
    auto* full_out = static_cast<std::int64_t*>(full_buf.ptr);
    auto* delta_out = static_cast<std::int64_t*>(delta_buf.ptr);

    constexpr std::uint8_t INT_KIND_FULL_V = 0;
    constexpr std::uint8_t INT_KIND_DELTA_V = 1;
    constexpr std::uint8_t INT_KIND_DELTA2_V = 2;

    const double precision_d = static_cast<double>(precision);
    std::size_t full_pos = 0;
    std::size_t delta_pos = 0;
    std::size_t delta_ref_pos = 0;
    std::size_t cross_ref_pos = 0;
    const std::size_t full_capacity = static_cast<std::size_t>(full_buf.size);
    const std::size_t delta_capacity = static_cast<std::size_t>(delta_buf.size);

    auto segment_ptr = [&](std::size_t island_idx) -> const double* {
        return intensity_ptr + static_cast<std::size_t>(base_ptr[scan_idx_ptr[island_idx]]) + static_cast<std::size_t>(start_idx_ptr[island_idx]);
    };
    auto segment_len = [&](std::size_t island_idx) -> std::size_t {
        return static_cast<std::size_t>(npts_ptr[island_idx]);
    };
    auto encode_len = [&](std::size_t curr_len, std::size_t ref_len) -> std::size_t {
        return use_max_ref_curr ? std::max(curr_len, ref_len) : curr_len;
    };
    auto scaled_at = [&](const double* ptr, std::size_t len, std::size_t pos) -> std::int64_t {
        return pos < len ? round_even_to_i64(ptr[pos] * precision_d) : static_cast<std::int64_t>(0);
    };

    {
        py::gil_scoped_release release;
        for (std::size_t track_idx = 0; track_idx < track_count; ++track_idx) {
            const auto track_start = static_cast<std::size_t>(track_ptr[track_idx]);
            const auto track_end = static_cast<std::size_t>(track_ptr[track_idx + 1]);
            std::vector<std::uint32_t> history_indices;
            history_indices.reserve(static_cast<std::size_t>(std::max(delta_ref_window, enable_second_order_delta ? 2 : 1)));

            for (std::size_t island_idx = track_start; island_idx < track_end; ++island_idx) {
                const auto curr_len = segment_len(island_idx);
                const auto* curr_ptr = segment_ptr(island_idx);
                const auto int_kind = kind_ptr[island_idx];

                if (int_kind == INT_KIND_FULL_V) {
                    if (full_pos + curr_len > full_capacity) {
                        throw std::runtime_error("fill_equal_fidelity_intensity_streams_compact_views full output overflow");
                    }
                    for (std::size_t i = 0; i < curr_len; ++i) {
                        full_out[full_pos + i] = round_even_to_i64(curr_ptr[i] * precision_d);
                    }
                    full_pos += curr_len;
                } else if (int_kind == INT_KIND_DELTA2_V) {
                    if (history_indices.size() < 2) {
                        throw std::runtime_error("fill_equal_fidelity_intensity_streams_compact_views delta2 missing history");
                    }
                    const auto prev1_idx = static_cast<std::size_t>(history_indices[history_indices.size() - 1]);
                    const auto prev2_idx = static_cast<std::size_t>(history_indices[history_indices.size() - 2]);
                    const auto* prev1_ptr = segment_ptr(prev1_idx);
                    const auto* prev2_ptr = segment_ptr(prev2_idx);
                    const auto prev1_len = segment_len(prev1_idx);
                    const auto prev2_len = segment_len(prev2_idx);
                    if (prev1_len != curr_len || prev2_len != curr_len) {
                        throw std::runtime_error("fill_equal_fidelity_intensity_streams_compact_views delta2 length mismatch");
                    }
                    if (delta_pos + curr_len > delta_capacity) {
                        throw std::runtime_error("fill_equal_fidelity_intensity_streams_compact_views delta2 output overflow");
                    }
                    for (std::size_t i = 0; i < curr_len; ++i) {
                        const auto curr64 = round_even_to_i64(curr_ptr[i] * precision_d);
                        const auto prev164 = round_even_to_i64(prev1_ptr[i] * precision_d);
                        const auto prev264 = round_even_to_i64(prev2_ptr[i] * precision_d);
                        delta_out[delta_pos + i] = curr64 - (2 * prev164) + prev264;
                    }
                    delta_pos += curr_len;
                } else if (int_kind == INT_KIND_DELTA_V) {
                    std::size_t ref_idx = 0;
                    const std::uint8_t ref_offset = delta_ref_pos < static_cast<std::size_t>(ref_buf.size)
                        ? ref_ptr[delta_ref_pos]
                        : static_cast<std::uint8_t>(1);
                    ++delta_ref_pos;
                    if (ref_offset == 0) {
                        if (cross_ref_pos >= static_cast<std::size_t>(cross_buf.size)) {
                            throw std::runtime_error("fill_equal_fidelity_intensity_streams_compact_views missing cross-track ref");
                        }
                        ref_idx = static_cast<std::size_t>(cross_ptr[cross_ref_pos]);
                        ++cross_ref_pos;
                    } else {
                        if (ref_offset > history_indices.size()) {
                            throw std::runtime_error("fill_equal_fidelity_intensity_streams_compact_views invalid in-track ref");
                        }
                        ref_idx = static_cast<std::size_t>(history_indices[history_indices.size() - ref_offset]);
                    }
                    if (ref_idx >= island_count) {
                        throw std::runtime_error("fill_equal_fidelity_intensity_streams_compact_views ref index out of range");
                    }
                    const auto* ref_segment_ptr = segment_ptr(ref_idx);
                    const auto ref_len = segment_len(ref_idx);
                    const auto out_len = encode_len(curr_len, ref_len);
                    if (delta_pos + out_len > delta_capacity) {
                        throw std::runtime_error("fill_equal_fidelity_intensity_streams_compact_views delta output overflow");
                    }
                    for (std::size_t i = 0; i < out_len; ++i) {
                        delta_out[delta_pos + i] = scaled_at(curr_ptr, curr_len, i) - scaled_at(ref_segment_ptr, ref_len, i);
                    }
                    delta_pos += out_len;
                } else {
                    throw std::runtime_error("fill_equal_fidelity_intensity_streams_compact_views unsupported int_kind");
                }

                history_indices.push_back(static_cast<std::uint32_t>(island_idx));
                const std::size_t history_limit = static_cast<std::size_t>(std::max(delta_ref_window, enable_second_order_delta ? 2 : 1));
                if (history_indices.size() > history_limit) {
                    history_indices.erase(history_indices.begin(), history_indices.begin() + static_cast<std::ptrdiff_t>(history_indices.size() - history_limit));
                }
            }
        }
    }

    if (full_pos != full_capacity) {
        throw std::runtime_error("fill_equal_fidelity_intensity_streams_compact_views full fill mismatch");
    }
    if (delta_pos != delta_capacity) {
        throw std::runtime_error("fill_equal_fidelity_intensity_streams_compact_views delta fill mismatch");
    }
    py::dict out;
    out["full_pos"] = py::int_(full_pos);
    out["delta_pos"] = py::int_(delta_pos);
    out["delta_ref_pos"] = py::int_(delta_ref_pos);
    out["cross_ref_pos"] = py::int_(cross_ref_pos);
    return out;
}

py::dict fill_strict_lossless_intensity_streams_compact_views(
    py::array_t<double, py::array::c_style | py::array::forcecast> intensity_data,
    py::array_t<std::uint64_t, py::array::c_style | py::array::forcecast> scan_base_offsets,
    py::array_t<std::uint32_t, py::array::c_style | py::array::forcecast> track_offsets,
    py::array_t<std::uint32_t, py::array::c_style | py::array::forcecast> scan_indices,
    py::array_t<std::uint32_t, py::array::c_style | py::array::forcecast> array_start_indices,
    py::array_t<std::uint32_t, py::array::c_style | py::array::forcecast> n_points,
    py::array_t<std::uint8_t, py::array::c_style | py::array::forcecast> int_kinds,
    py::array_t<std::uint8_t, py::array::c_style | py::array::forcecast> delta_ref_offsets,
    py::array_t<std::uint32_t, py::array::c_style | py::array::forcecast> cross_track_ref_indices,
    py::array_t<double, py::array::c_style | py::array::forcecast> full_float64,
    py::array_t<double, py::array::c_style | py::array::forcecast> delta_float64,
    bool use_max_ref_curr,
    bool enable_second_order_delta,
    int delta_ref_window
) {
    auto int_buf = intensity_data.request();
    auto base_buf = scan_base_offsets.request();
    auto track_buf = track_offsets.request();
    auto scan_buf = scan_indices.request();
    auto start_buf = array_start_indices.request();
    auto npts_buf = n_points.request();
    auto kind_buf = int_kinds.request();
    auto ref_buf = delta_ref_offsets.request();
    auto cross_buf = cross_track_ref_indices.request();
    auto full_buf = full_float64.request();
    auto delta_buf = delta_float64.request();

    const auto island_count = static_cast<std::size_t>(scan_buf.size);
    if (
        island_count != static_cast<std::size_t>(start_buf.size)
        || island_count != static_cast<std::size_t>(npts_buf.size)
        || island_count != static_cast<std::size_t>(kind_buf.size)
    ) {
        throw std::runtime_error("fill_strict_lossless_intensity_streams_compact_views island metadata length mismatch");
    }
    const auto track_count = static_cast<std::size_t>(track_buf.size == 0 ? 0 : track_buf.size - 1);
    if (track_count == 0 && island_count != 0) {
        throw std::runtime_error("fill_strict_lossless_intensity_streams_compact_views empty track_offsets with nonzero islands");
    }

    const auto* intensity_ptr = static_cast<const double*>(int_buf.ptr);
    const auto* base_ptr = static_cast<const std::uint64_t*>(base_buf.ptr);
    const auto* track_ptr = static_cast<const std::uint32_t*>(track_buf.ptr);
    const auto* scan_idx_ptr = static_cast<const std::uint32_t*>(scan_buf.ptr);
    const auto* start_idx_ptr = static_cast<const std::uint32_t*>(start_buf.ptr);
    const auto* npts_ptr = static_cast<const std::uint32_t*>(npts_buf.ptr);
    const auto* kind_ptr = static_cast<const std::uint8_t*>(kind_buf.ptr);
    const auto* ref_ptr = static_cast<const std::uint8_t*>(ref_buf.ptr);
    const auto* cross_ptr = static_cast<const std::uint32_t*>(cross_buf.ptr);
    auto* full_out = static_cast<double*>(full_buf.ptr);
    auto* delta_out = static_cast<double*>(delta_buf.ptr);

    constexpr std::uint8_t INT_KIND_FULL_V = 0;
    constexpr std::uint8_t INT_KIND_DELTA_V = 1;
    constexpr std::uint8_t INT_KIND_DELTA2_V = 2;

    std::size_t full_pos = 0;
    std::size_t delta_pos = 0;
    std::size_t delta_ref_pos = 0;
    std::size_t cross_ref_pos = 0;
    const std::size_t full_capacity = static_cast<std::size_t>(full_buf.size);
    const std::size_t delta_capacity = static_cast<std::size_t>(delta_buf.size);

    auto segment_ptr = [&](std::size_t island_idx) -> const double* {
        return intensity_ptr + static_cast<std::size_t>(base_ptr[scan_idx_ptr[island_idx]]) + static_cast<std::size_t>(start_idx_ptr[island_idx]);
    };
    auto segment_len = [&](std::size_t island_idx) -> std::size_t {
        return static_cast<std::size_t>(npts_ptr[island_idx]);
    };
    auto encode_len = [&](std::size_t curr_len, std::size_t ref_len) -> std::size_t {
        return use_max_ref_curr ? std::max(curr_len, ref_len) : curr_len;
    };
    auto value_at = [&](const double* ptr, std::size_t len, std::size_t pos) -> double {
        return pos < len ? ptr[pos] : 0.0;
    };

    {
        py::gil_scoped_release release;
        for (std::size_t track_idx = 0; track_idx < track_count; ++track_idx) {
            const auto track_start = static_cast<std::size_t>(track_ptr[track_idx]);
            const auto track_end = static_cast<std::size_t>(track_ptr[track_idx + 1]);
            std::vector<std::uint32_t> history_indices;
            history_indices.reserve(static_cast<std::size_t>(std::max(delta_ref_window, enable_second_order_delta ? 2 : 1)));

            for (std::size_t island_idx = track_start; island_idx < track_end; ++island_idx) {
                const auto curr_len = segment_len(island_idx);
                const auto* curr_ptr = segment_ptr(island_idx);
                const auto int_kind = kind_ptr[island_idx];

                if (int_kind == INT_KIND_FULL_V) {
                    if (full_pos + curr_len > full_capacity) {
                        throw std::runtime_error("fill_strict_lossless_intensity_streams_compact_views full output overflow");
                    }
                    for (std::size_t i = 0; i < curr_len; ++i) {
                        full_out[full_pos + i] = curr_ptr[i];
                    }
                    full_pos += curr_len;
                } else if (int_kind == INT_KIND_DELTA2_V) {
                    if (history_indices.size() < 2) {
                        throw std::runtime_error("fill_strict_lossless_intensity_streams_compact_views delta2 missing history");
                    }
                    const auto prev1_idx = static_cast<std::size_t>(history_indices[history_indices.size() - 1]);
                    const auto prev2_idx = static_cast<std::size_t>(history_indices[history_indices.size() - 2]);
                    const auto* prev1_ptr = segment_ptr(prev1_idx);
                    const auto* prev2_ptr = segment_ptr(prev2_idx);
                    const auto prev1_len = segment_len(prev1_idx);
                    const auto prev2_len = segment_len(prev2_idx);
                    if (prev1_len != curr_len || prev2_len != curr_len) {
                        throw std::runtime_error("fill_strict_lossless_intensity_streams_compact_views delta2 length mismatch");
                    }
                    if (delta_pos + curr_len > delta_capacity) {
                        throw std::runtime_error("fill_strict_lossless_intensity_streams_compact_views delta2 output overflow");
                    }
                    for (std::size_t i = 0; i < curr_len; ++i) {
                        delta_out[delta_pos + i] = curr_ptr[i] - (2.0 * prev1_ptr[i]) + prev2_ptr[i];
                    }
                    delta_pos += curr_len;
                } else if (int_kind == INT_KIND_DELTA_V) {
                    std::size_t ref_idx = 0;
                    const std::uint8_t ref_offset = delta_ref_pos < static_cast<std::size_t>(ref_buf.size)
                        ? ref_ptr[delta_ref_pos]
                        : static_cast<std::uint8_t>(1);
                    ++delta_ref_pos;
                    if (ref_offset == 0) {
                        if (cross_ref_pos >= static_cast<std::size_t>(cross_buf.size)) {
                            throw std::runtime_error("fill_strict_lossless_intensity_streams_compact_views missing cross-track ref");
                        }
                        ref_idx = static_cast<std::size_t>(cross_ptr[cross_ref_pos]);
                        ++cross_ref_pos;
                    } else {
                        if (ref_offset > history_indices.size()) {
                            throw std::runtime_error("fill_strict_lossless_intensity_streams_compact_views invalid in-track ref");
                        }
                        ref_idx = static_cast<std::size_t>(history_indices[history_indices.size() - ref_offset]);
                    }
                    if (ref_idx >= island_count) {
                        throw std::runtime_error("fill_strict_lossless_intensity_streams_compact_views ref index out of range");
                    }
                    const auto* ref_segment_ptr = segment_ptr(ref_idx);
                    const auto ref_len = segment_len(ref_idx);
                    const auto out_len = encode_len(curr_len, ref_len);
                    if (delta_pos + out_len > delta_capacity) {
                        throw std::runtime_error("fill_strict_lossless_intensity_streams_compact_views delta output overflow");
                    }
                    for (std::size_t i = 0; i < out_len; ++i) {
                        delta_out[delta_pos + i] = value_at(curr_ptr, curr_len, i) - value_at(ref_segment_ptr, ref_len, i);
                    }
                    delta_pos += out_len;
                } else {
                    throw std::runtime_error("fill_strict_lossless_intensity_streams_compact_views unsupported int_kind");
                }

                history_indices.push_back(static_cast<std::uint32_t>(island_idx));
                const std::size_t history_limit = static_cast<std::size_t>(std::max(delta_ref_window, enable_second_order_delta ? 2 : 1));
                if (history_indices.size() > history_limit) {
                    history_indices.erase(history_indices.begin(), history_indices.begin() + static_cast<std::ptrdiff_t>(history_indices.size() - history_limit));
                }
            }
        }
    }

    if (full_pos != full_capacity) {
        throw std::runtime_error("fill_strict_lossless_intensity_streams_compact_views full fill mismatch");
    }
    if (delta_pos != delta_capacity) {
        throw std::runtime_error("fill_strict_lossless_intensity_streams_compact_views delta fill mismatch");
    }
    py::dict out;
    out["full_pos"] = py::int_(full_pos);
    out["delta_pos"] = py::int_(delta_pos);
    out["delta_ref_pos"] = py::int_(delta_ref_pos);
    out["cross_ref_pos"] = py::int_(cross_ref_pos);
    return out;
}

py::tuple ms1_expand_equal_fidelity_intensity_by_scan_f64(
    py::array_t<std::uint32_t, py::array::c_style | py::array::forcecast> scan_lengths,
    py::array_t<std::uint32_t, py::array::c_style | py::array::forcecast> track_lengths,
    py::array_t<std::uint32_t, py::array::c_style | py::array::forcecast> scan_indices,
    py::array_t<std::uint32_t, py::array::c_style | py::array::forcecast> array_start_indices,
    py::array_t<std::uint32_t, py::array::c_style | py::array::forcecast> n_points,
    py::array_t<std::uint8_t, py::array::c_style | py::array::forcecast> int_kinds,
    py::array_t<std::uint8_t, py::array::c_style | py::array::forcecast> delta_ref_offsets,
    py::array_t<std::uint32_t, py::array::c_style | py::array::forcecast> cross_track_ref_indices,
    py::array_t<std::int64_t, py::array::c_style | py::array::forcecast> full_codes,
    py::array_t<std::int64_t, py::array::c_style | py::array::forcecast> delta_codes,
    int precision,
    bool exact_scaled_intensity,
    bool use_max_ref_curr,
    bool enable_second_order_delta,
    int delta_ref_window
) {
    auto scan_len_buf = scan_lengths.request();
    auto track_buf = track_lengths.request();
    auto scan_buf = scan_indices.request();
    auto start_buf = array_start_indices.request();
    auto npts_buf = n_points.request();
    auto kind_buf = int_kinds.request();
    auto ref_buf = delta_ref_offsets.request();
    auto cross_buf = cross_track_ref_indices.request();
    auto full_buf = full_codes.request();
    auto delta_buf = delta_codes.request();

    if (
        scan_len_buf.ndim != 1 || track_buf.ndim != 1 || scan_buf.ndim != 1 ||
        start_buf.ndim != 1 || npts_buf.ndim != 1 || kind_buf.ndim != 1 ||
        ref_buf.ndim != 1 || cross_buf.ndim != 1 || full_buf.ndim != 1 ||
        delta_buf.ndim != 1
    ) {
        throw std::runtime_error("ms1_expand_equal_fidelity_intensity_by_scan_f64 expects 1D arrays");
    }

    const auto n_scans = static_cast<std::size_t>(scan_len_buf.shape[0]);
    const auto island_count = static_cast<std::size_t>(scan_buf.shape[0]);
    if (
        static_cast<std::size_t>(start_buf.shape[0]) != island_count ||
        static_cast<std::size_t>(npts_buf.shape[0]) != island_count ||
        static_cast<std::size_t>(kind_buf.shape[0]) != island_count
    ) {
        throw std::runtime_error("ms1_expand_equal_fidelity_intensity_by_scan_f64 island metadata length mismatch");
    }
    if (precision == 0) {
        throw std::runtime_error("ms1_expand_equal_fidelity_intensity_by_scan_f64 precision must be non-zero");
    }

    const auto* scan_len_ptr = static_cast<const std::uint32_t*>(scan_len_buf.ptr);
    const auto* track_ptr = static_cast<const std::uint32_t*>(track_buf.ptr);
    const auto* scan_idx_ptr = static_cast<const std::uint32_t*>(scan_buf.ptr);
    const auto* start_idx_ptr = static_cast<const std::uint32_t*>(start_buf.ptr);
    const auto* npts_ptr = static_cast<const std::uint32_t*>(npts_buf.ptr);
    const auto* kind_ptr = static_cast<const std::uint8_t*>(kind_buf.ptr);
    const auto* ref_ptr = static_cast<const std::uint8_t*>(ref_buf.ptr);
    const auto* cross_ptr = static_cast<const std::uint32_t*>(cross_buf.ptr);
    const auto* full_ptr = static_cast<const std::int64_t*>(full_buf.ptr);
    const auto* delta_ptr = static_cast<const std::int64_t*>(delta_buf.ptr);

    auto offsets = py::array_t<std::uint64_t>(n_scans + 1U);
    auto* offset_ptr = offsets.mutable_data();
    offset_ptr[0] = 0U;
    for (std::size_t scan = 0; scan < n_scans; ++scan) {
        offset_ptr[scan + 1U] = offset_ptr[scan] + static_cast<std::uint64_t>(scan_len_ptr[scan]);
    }
    const auto total_points = static_cast<std::size_t>(offset_ptr[n_scans]);
    auto intensity_out = py::array_t<double>(total_points);
    auto* out_ptr = intensity_out.mutable_data();
    std::fill(out_ptr, out_ptr + total_points, 0.0);

    constexpr std::uint8_t INT_KIND_FULL_V = 0;
    constexpr std::uint8_t INT_KIND_DELTA_V = 1;
    constexpr std::uint8_t INT_KIND_DELTA2_V = 2;

    const double precision_d = static_cast<double>(precision);
    const std::size_t track_count = static_cast<std::size_t>(track_buf.shape[0]);
    const std::size_t history_limit = static_cast<std::size_t>(std::max(delta_ref_window, enable_second_order_delta ? 2 : 1));
    std::unordered_set<std::size_t> needed_cross_refs;
    needed_cross_refs.reserve(static_cast<std::size_t>(cross_buf.shape[0]) * 2U + 1U);
    for (std::size_t i = 0; i < static_cast<std::size_t>(cross_buf.shape[0]); ++i) {
        needed_cross_refs.insert(static_cast<std::size_t>(cross_ptr[i]));
    }

    auto decode_code = [&](std::int64_t code) -> double {
        if (exact_scaled_intensity || code >= 0) {
            return static_cast<double>(code) / precision_d;
        }
        return std::pow(2.0, -static_cast<double>(code) / 100000.0) / precision_d;
    };
    auto code_at = [](const std::shared_ptr<std::vector<std::int64_t>>& codes, std::size_t pos) -> std::int64_t {
        return (codes && pos < codes->size()) ? (*codes)[pos] : static_cast<std::int64_t>(0);
    };

    std::size_t island_pos = 0;
    std::size_t full_pos = 0;
    std::size_t delta_pos = 0;
    std::size_t delta_ref_pos = 0;
    std::size_t cross_ref_pos = 0;
    std::unordered_map<std::size_t, std::shared_ptr<std::vector<std::int64_t>>> cross_history;
    cross_history.reserve(needed_cross_refs.size() * 2U + 1U);

    {
        py::gil_scoped_release release;
        for (std::size_t track_idx = 0; track_idx < track_count; ++track_idx) {
            std::vector<std::shared_ptr<std::vector<std::int64_t>>> history;
            history.reserve(history_limit);
            const auto track_len = static_cast<std::size_t>(track_ptr[track_idx]);
            for (std::size_t local = 0; local < track_len; ++local) {
                if (island_pos >= island_count) {
                    throw std::runtime_error("ms1_expand_equal_fidelity_intensity_by_scan_f64 track length exceeds island count");
                }
                const auto curr_len = static_cast<std::size_t>(npts_ptr[island_pos]);
                const auto int_kind = kind_ptr[island_pos];
                auto curr_codes = std::make_shared<std::vector<std::int64_t>>(curr_len);

                if (int_kind == INT_KIND_FULL_V) {
                    if (full_pos + curr_len > static_cast<std::size_t>(full_buf.shape[0])) {
                        throw std::runtime_error("ms1_expand_equal_fidelity_intensity_by_scan_f64 full stream underflow");
                    }
                    std::copy(full_ptr + full_pos, full_ptr + full_pos + curr_len, curr_codes->begin());
                    full_pos += curr_len;
                } else if (int_kind == INT_KIND_DELTA2_V) {
                    if (history.size() < 2U) {
                        throw std::runtime_error("ms1_expand_equal_fidelity_intensity_by_scan_f64 delta2 missing history");
                    }
                    if (delta_pos + curr_len > static_cast<std::size_t>(delta_buf.shape[0])) {
                        throw std::runtime_error("ms1_expand_equal_fidelity_intensity_by_scan_f64 delta2 stream underflow");
                    }
                    const auto& prev1 = history[history.size() - 1U];
                    const auto& prev2 = history[history.size() - 2U];
                    for (std::size_t i = 0; i < curr_len; ++i) {
                        (*curr_codes)[i] = delta_ptr[delta_pos + i] + (2 * code_at(prev1, i)) - code_at(prev2, i);
                    }
                    delta_pos += curr_len;
                } else if (int_kind == INT_KIND_DELTA_V) {
                    const std::uint8_t ref_offset = delta_ref_pos < static_cast<std::size_t>(ref_buf.shape[0])
                        ? ref_ptr[delta_ref_pos]
                        : static_cast<std::uint8_t>(1);
                    ++delta_ref_pos;
                    std::shared_ptr<std::vector<std::int64_t>> ref_codes;
                    if (ref_offset == 0) {
                        if (cross_ref_pos >= static_cast<std::size_t>(cross_buf.shape[0])) {
                            throw std::runtime_error("ms1_expand_equal_fidelity_intensity_by_scan_f64 missing cross-track ref");
                        }
                        const auto ref_idx = static_cast<std::size_t>(cross_ptr[cross_ref_pos++]);
                        auto it = cross_history.find(ref_idx);
                        if (it == cross_history.end()) {
                            throw std::runtime_error("ms1_expand_equal_fidelity_intensity_by_scan_f64 invalid cross-track ref");
                        }
                        ref_codes = it->second;
                    } else {
                        if (ref_offset > history.size()) {
                            throw std::runtime_error("ms1_expand_equal_fidelity_intensity_by_scan_f64 invalid in-track ref");
                        }
                        ref_codes = history[history.size() - ref_offset];
                    }
                    const auto ref_len = ref_codes ? ref_codes->size() : 0U;
                    const auto encoded_len = use_max_ref_curr ? std::max(curr_len, ref_len) : curr_len;
                    if (delta_pos + encoded_len > static_cast<std::size_t>(delta_buf.shape[0])) {
                        throw std::runtime_error("ms1_expand_equal_fidelity_intensity_by_scan_f64 delta stream underflow");
                    }
                    for (std::size_t i = 0; i < curr_len; ++i) {
                        (*curr_codes)[i] = code_at(ref_codes, i) + delta_ptr[delta_pos + i];
                    }
                    delta_pos += encoded_len;
                } else {
                    throw std::runtime_error("ms1_expand_equal_fidelity_intensity_by_scan_f64 unsupported int_kind");
                }

                const auto scan_idx = static_cast<std::size_t>(scan_idx_ptr[island_pos]);
                const auto start = static_cast<std::size_t>(start_idx_ptr[island_pos]);
                if (scan_idx >= n_scans || start + curr_len > static_cast<std::size_t>(scan_len_ptr[scan_idx])) {
                    throw std::runtime_error("ms1_expand_equal_fidelity_intensity_by_scan_f64 island overruns scan");
                }
                auto* target = out_ptr + static_cast<std::size_t>(offset_ptr[scan_idx]) + start;
                for (std::size_t i = 0; i < curr_len; ++i) {
                    target[i] = decode_code((*curr_codes)[i]);
                }

                history.push_back(curr_codes);
                if (history.size() > history_limit) {
                    history.erase(history.begin(), history.begin() + static_cast<std::ptrdiff_t>(history.size() - history_limit));
                }
                if (needed_cross_refs.find(island_pos) != needed_cross_refs.end()) {
                    cross_history[island_pos] = curr_codes;
                }
                ++island_pos;
            }
        }
    }

    if (island_pos != island_count) {
        throw std::runtime_error("ms1_expand_equal_fidelity_intensity_by_scan_f64 island count mismatch");
    }
    if (full_pos != static_cast<std::size_t>(full_buf.shape[0])) {
        throw std::runtime_error("ms1_expand_equal_fidelity_intensity_by_scan_f64 full stream mismatch");
    }
    if (delta_pos != static_cast<std::size_t>(delta_buf.shape[0])) {
        throw std::runtime_error("ms1_expand_equal_fidelity_intensity_by_scan_f64 delta stream mismatch");
    }
    return py::make_tuple(offsets, intensity_out);
}

py::tuple ms1_expand_float64_intensity_by_scan_f64(
    py::array_t<std::uint32_t, py::array::c_style | py::array::forcecast> scan_lengths,
    py::array_t<std::uint32_t, py::array::c_style | py::array::forcecast> track_lengths,
    py::array_t<std::uint32_t, py::array::c_style | py::array::forcecast> scan_indices,
    py::array_t<std::uint32_t, py::array::c_style | py::array::forcecast> array_start_indices,
    py::array_t<std::uint32_t, py::array::c_style | py::array::forcecast> n_points,
    py::array_t<std::uint8_t, py::array::c_style | py::array::forcecast> int_kinds,
    py::array_t<std::uint8_t, py::array::c_style | py::array::forcecast> delta_ref_offsets,
    py::array_t<std::uint32_t, py::array::c_style | py::array::forcecast> cross_track_ref_indices,
    py::array_t<double, py::array::c_style | py::array::forcecast> full_values,
    py::array_t<double, py::array::c_style | py::array::forcecast> delta_values,
    bool use_max_ref_curr,
    bool enable_second_order_delta,
    int delta_ref_window
) {
    auto scan_len_buf = scan_lengths.request();
    auto track_buf = track_lengths.request();
    auto scan_buf = scan_indices.request();
    auto start_buf = array_start_indices.request();
    auto npts_buf = n_points.request();
    auto kind_buf = int_kinds.request();
    auto ref_buf = delta_ref_offsets.request();
    auto cross_buf = cross_track_ref_indices.request();
    auto full_buf = full_values.request();
    auto delta_buf = delta_values.request();

    if (
        scan_len_buf.ndim != 1 || track_buf.ndim != 1 || scan_buf.ndim != 1 ||
        start_buf.ndim != 1 || npts_buf.ndim != 1 || kind_buf.ndim != 1 ||
        ref_buf.ndim != 1 || cross_buf.ndim != 1 || full_buf.ndim != 1 ||
        delta_buf.ndim != 1
    ) {
        throw std::runtime_error("ms1_expand_float64_intensity_by_scan_f64 expects 1D arrays");
    }

    const auto n_scans = static_cast<std::size_t>(scan_len_buf.shape[0]);
    const auto island_count = static_cast<std::size_t>(scan_buf.shape[0]);
    if (
        static_cast<std::size_t>(start_buf.shape[0]) != island_count ||
        static_cast<std::size_t>(npts_buf.shape[0]) != island_count ||
        static_cast<std::size_t>(kind_buf.shape[0]) != island_count
    ) {
        throw std::runtime_error("ms1_expand_float64_intensity_by_scan_f64 island metadata length mismatch");
    }

    const auto* scan_len_ptr = static_cast<const std::uint32_t*>(scan_len_buf.ptr);
    const auto* track_ptr = static_cast<const std::uint32_t*>(track_buf.ptr);
    const auto* scan_idx_ptr = static_cast<const std::uint32_t*>(scan_buf.ptr);
    const auto* start_idx_ptr = static_cast<const std::uint32_t*>(start_buf.ptr);
    const auto* npts_ptr = static_cast<const std::uint32_t*>(npts_buf.ptr);
    const auto* kind_ptr = static_cast<const std::uint8_t*>(kind_buf.ptr);
    const auto* ref_ptr = static_cast<const std::uint8_t*>(ref_buf.ptr);
    const auto* cross_ptr = static_cast<const std::uint32_t*>(cross_buf.ptr);
    const auto* full_ptr = static_cast<const double*>(full_buf.ptr);
    const auto* delta_ptr = static_cast<const double*>(delta_buf.ptr);

    auto offsets = py::array_t<std::uint64_t>(n_scans + 1U);
    auto* offset_ptr = offsets.mutable_data();
    offset_ptr[0] = 0U;
    for (std::size_t scan = 0; scan < n_scans; ++scan) {
        offset_ptr[scan + 1U] = offset_ptr[scan] + static_cast<std::uint64_t>(scan_len_ptr[scan]);
    }
    const auto total_points = static_cast<std::size_t>(offset_ptr[n_scans]);
    auto intensity_out = py::array_t<double>(total_points);
    auto* out_ptr = intensity_out.mutable_data();
    std::fill(out_ptr, out_ptr + total_points, 0.0);

    constexpr std::uint8_t INT_KIND_FULL_V = 0;
    constexpr std::uint8_t INT_KIND_DELTA_V = 1;
    constexpr std::uint8_t INT_KIND_DELTA2_V = 2;

    const std::size_t track_count = static_cast<std::size_t>(track_buf.shape[0]);
    const std::size_t history_limit = static_cast<std::size_t>(std::max(delta_ref_window, enable_second_order_delta ? 2 : 1));

    auto segment_ptr = [&](std::size_t island_idx) -> const double* {
        const auto scan_idx = static_cast<std::size_t>(scan_idx_ptr[island_idx]);
        const auto start = static_cast<std::size_t>(start_idx_ptr[island_idx]);
        return out_ptr + static_cast<std::size_t>(offset_ptr[scan_idx]) + start;
    };
    auto segment_len = [&](std::size_t island_idx) -> std::size_t {
        return static_cast<std::size_t>(npts_ptr[island_idx]);
    };
    auto value_at = [](const double* ptr, std::size_t len, std::size_t pos) -> double {
        return pos < len ? ptr[pos] : 0.0;
    };

    std::size_t island_pos = 0;
    std::size_t full_pos = 0;
    std::size_t delta_pos = 0;
    std::size_t delta_ref_pos = 0;
    std::size_t cross_ref_pos = 0;

    {
        py::gil_scoped_release release;
        for (std::size_t track_idx = 0; track_idx < track_count; ++track_idx) {
            std::vector<std::uint32_t> history;
            history.reserve(history_limit);
            const auto track_len = static_cast<std::size_t>(track_ptr[track_idx]);
            for (std::size_t local = 0; local < track_len; ++local) {
                if (island_pos >= island_count) {
                    throw std::runtime_error("ms1_expand_float64_intensity_by_scan_f64 track length exceeds island count");
                }
                const auto curr_len = segment_len(island_pos);
                const auto scan_idx = static_cast<std::size_t>(scan_idx_ptr[island_pos]);
                const auto start = static_cast<std::size_t>(start_idx_ptr[island_pos]);
                if (scan_idx >= n_scans || start + curr_len > static_cast<std::size_t>(scan_len_ptr[scan_idx])) {
                    throw std::runtime_error("ms1_expand_float64_intensity_by_scan_f64 island overruns scan");
                }
                auto* target = out_ptr + static_cast<std::size_t>(offset_ptr[scan_idx]) + start;
                const auto int_kind = kind_ptr[island_pos];

                if (int_kind == INT_KIND_FULL_V) {
                    if (full_pos + curr_len > static_cast<std::size_t>(full_buf.shape[0])) {
                        throw std::runtime_error("ms1_expand_float64_intensity_by_scan_f64 full stream underflow");
                    }
                    std::copy(full_ptr + full_pos, full_ptr + full_pos + curr_len, target);
                    full_pos += curr_len;
                } else if (int_kind == INT_KIND_DELTA2_V) {
                    if (history.size() < 2U) {
                        throw std::runtime_error("ms1_expand_float64_intensity_by_scan_f64 delta2 missing history");
                    }
                    if (delta_pos + curr_len > static_cast<std::size_t>(delta_buf.shape[0])) {
                        throw std::runtime_error("ms1_expand_float64_intensity_by_scan_f64 delta2 stream underflow");
                    }
                    const auto prev1_idx = static_cast<std::size_t>(history[history.size() - 1U]);
                    const auto prev2_idx = static_cast<std::size_t>(history[history.size() - 2U]);
                    const auto* prev1 = segment_ptr(prev1_idx);
                    const auto* prev2 = segment_ptr(prev2_idx);
                    const auto prev1_len = segment_len(prev1_idx);
                    const auto prev2_len = segment_len(prev2_idx);
                    for (std::size_t i = 0; i < curr_len; ++i) {
                        target[i] = delta_ptr[delta_pos + i] + (2.0 * value_at(prev1, prev1_len, i)) - value_at(prev2, prev2_len, i);
                    }
                    delta_pos += curr_len;
                } else if (int_kind == INT_KIND_DELTA_V) {
                    const std::uint8_t ref_offset = delta_ref_pos < static_cast<std::size_t>(ref_buf.shape[0])
                        ? ref_ptr[delta_ref_pos]
                        : static_cast<std::uint8_t>(1);
                    ++delta_ref_pos;
                    std::size_t ref_idx = 0;
                    if (ref_offset == 0) {
                        if (cross_ref_pos >= static_cast<std::size_t>(cross_buf.shape[0])) {
                            throw std::runtime_error("ms1_expand_float64_intensity_by_scan_f64 missing cross-track ref");
                        }
                        ref_idx = static_cast<std::size_t>(cross_ptr[cross_ref_pos++]);
                        if (ref_idx >= island_pos) {
                            throw std::runtime_error("ms1_expand_float64_intensity_by_scan_f64 invalid cross-track ref");
                        }
                    } else {
                        if (ref_offset > history.size()) {
                            throw std::runtime_error("ms1_expand_float64_intensity_by_scan_f64 invalid in-track ref");
                        }
                        ref_idx = static_cast<std::size_t>(history[history.size() - ref_offset]);
                    }
                    if (ref_idx >= island_count) {
                        throw std::runtime_error("ms1_expand_float64_intensity_by_scan_f64 ref index out of range");
                    }
                    const auto* ref = segment_ptr(ref_idx);
                    const auto ref_len = segment_len(ref_idx);
                    const auto encoded_len = use_max_ref_curr ? std::max(curr_len, ref_len) : curr_len;
                    if (delta_pos + encoded_len > static_cast<std::size_t>(delta_buf.shape[0])) {
                        throw std::runtime_error("ms1_expand_float64_intensity_by_scan_f64 delta stream underflow");
                    }
                    for (std::size_t i = 0; i < curr_len; ++i) {
                        target[i] = value_at(ref, ref_len, i) + delta_ptr[delta_pos + i];
                    }
                    delta_pos += encoded_len;
                } else {
                    throw std::runtime_error("ms1_expand_float64_intensity_by_scan_f64 unsupported int_kind");
                }

                history.push_back(static_cast<std::uint32_t>(island_pos));
                if (history.size() > history_limit) {
                    history.erase(history.begin(), history.begin() + static_cast<std::ptrdiff_t>(history.size() - history_limit));
                }
                ++island_pos;
            }
        }
    }

    if (island_pos != island_count) {
        throw std::runtime_error("ms1_expand_float64_intensity_by_scan_f64 island count mismatch");
    }
    if (full_pos != static_cast<std::size_t>(full_buf.shape[0])) {
        throw std::runtime_error("ms1_expand_float64_intensity_by_scan_f64 full stream mismatch");
    }
    if (delta_pos != static_cast<std::size_t>(delta_buf.shape[0])) {
        throw std::runtime_error("ms1_expand_float64_intensity_by_scan_f64 delta stream mismatch");
    }
    return py::make_tuple(offsets, intensity_out);
}

py::tuple ms1_expand_equal_fidelity_island_intensity_f64(
    py::array_t<std::uint32_t, py::array::c_style | py::array::forcecast> track_lengths,
    py::array_t<std::uint32_t, py::array::c_style | py::array::forcecast> n_points,
    py::array_t<std::uint8_t, py::array::c_style | py::array::forcecast> int_kinds,
    py::array_t<std::uint8_t, py::array::c_style | py::array::forcecast> delta_ref_offsets,
    py::array_t<std::uint32_t, py::array::c_style | py::array::forcecast> cross_track_ref_indices,
    py::array_t<std::int64_t, py::array::c_style | py::array::forcecast> full_codes,
    py::array_t<std::int64_t, py::array::c_style | py::array::forcecast> delta_codes,
    int precision,
    bool exact_scaled_intensity,
    bool use_max_ref_curr,
    bool enable_second_order_delta,
    int delta_ref_window
) {
    auto track_buf = track_lengths.request();
    auto npts_buf = n_points.request();
    auto kind_buf = int_kinds.request();
    auto ref_buf = delta_ref_offsets.request();
    auto cross_buf = cross_track_ref_indices.request();
    auto full_buf = full_codes.request();
    auto delta_buf = delta_codes.request();
    if (
        track_buf.ndim != 1 || npts_buf.ndim != 1 || kind_buf.ndim != 1 ||
        ref_buf.ndim != 1 || cross_buf.ndim != 1 || full_buf.ndim != 1 ||
        delta_buf.ndim != 1
    ) {
        throw std::runtime_error("ms1_expand_equal_fidelity_island_intensity_f64 expects 1D arrays");
    }
    const auto island_count = static_cast<std::size_t>(npts_buf.shape[0]);
    if (static_cast<std::size_t>(kind_buf.shape[0]) != island_count) {
        throw std::runtime_error("ms1_expand_equal_fidelity_island_intensity_f64 island metadata length mismatch");
    }
    if (precision == 0) {
        throw std::runtime_error("ms1_expand_equal_fidelity_island_intensity_f64 precision must be non-zero");
    }
    const auto* track_ptr = static_cast<const std::uint32_t*>(track_buf.ptr);
    const auto* npts_ptr = static_cast<const std::uint32_t*>(npts_buf.ptr);
    const auto* kind_ptr = static_cast<const std::uint8_t*>(kind_buf.ptr);
    const auto* ref_ptr = static_cast<const std::uint8_t*>(ref_buf.ptr);
    const auto* cross_ptr = static_cast<const std::uint32_t*>(cross_buf.ptr);
    const auto* full_ptr = static_cast<const std::int64_t*>(full_buf.ptr);
    const auto* delta_ptr = static_cast<const std::int64_t*>(delta_buf.ptr);

    auto offsets = py::array_t<std::uint64_t>(island_count + 1U);
    auto* offset_ptr = offsets.mutable_data();
    offset_ptr[0] = 0U;
    for (std::size_t i = 0; i < island_count; ++i) {
        offset_ptr[i + 1U] = offset_ptr[i] + static_cast<std::uint64_t>(npts_ptr[i]);
    }
    const auto total_values = static_cast<std::size_t>(offset_ptr[island_count]);
    auto flat_intensity = py::array_t<double>(total_values);
    auto* out_ptr = flat_intensity.mutable_data();

    constexpr std::uint8_t INT_KIND_FULL_V = 0;
    constexpr std::uint8_t INT_KIND_DELTA_V = 1;
    constexpr std::uint8_t INT_KIND_DELTA2_V = 2;

    const double precision_d = static_cast<double>(precision);
    const std::size_t track_count = static_cast<std::size_t>(track_buf.shape[0]);
    const std::size_t history_limit = static_cast<std::size_t>(std::max(delta_ref_window, enable_second_order_delta ? 2 : 1));
    std::unordered_set<std::size_t> needed_cross_refs;
    needed_cross_refs.reserve(static_cast<std::size_t>(cross_buf.shape[0]) * 2U + 1U);
    for (std::size_t i = 0; i < static_cast<std::size_t>(cross_buf.shape[0]); ++i) {
        needed_cross_refs.insert(static_cast<std::size_t>(cross_ptr[i]));
    }

    auto decode_code = [&](std::int64_t code) -> double {
        if (exact_scaled_intensity || code >= 0) {
            return static_cast<double>(code) / precision_d;
        }
        return std::pow(2.0, -static_cast<double>(code) / 100000.0) / precision_d;
    };
    auto code_at = [](const std::shared_ptr<std::vector<std::int64_t>>& codes, std::size_t pos) -> std::int64_t {
        return (codes && pos < codes->size()) ? (*codes)[pos] : static_cast<std::int64_t>(0);
    };

    std::size_t island_pos = 0;
    std::size_t full_pos = 0;
    std::size_t delta_pos = 0;
    std::size_t delta_ref_pos = 0;
    std::size_t cross_ref_pos = 0;
    std::unordered_map<std::size_t, std::shared_ptr<std::vector<std::int64_t>>> cross_history;
    cross_history.reserve(needed_cross_refs.size() * 2U + 1U);

    {
        py::gil_scoped_release release;
        for (std::size_t track_idx = 0; track_idx < track_count; ++track_idx) {
            std::vector<std::shared_ptr<std::vector<std::int64_t>>> history;
            history.reserve(history_limit);
            const auto track_len = static_cast<std::size_t>(track_ptr[track_idx]);
            for (std::size_t local = 0; local < track_len; ++local) {
                if (island_pos >= island_count) {
                    throw std::runtime_error("ms1_expand_equal_fidelity_island_intensity_f64 track length exceeds island count");
                }
                const auto curr_len = static_cast<std::size_t>(npts_ptr[island_pos]);
                const auto int_kind = kind_ptr[island_pos];
                auto curr_codes = std::make_shared<std::vector<std::int64_t>>(curr_len);
                if (int_kind == INT_KIND_FULL_V) {
                    if (full_pos + curr_len > static_cast<std::size_t>(full_buf.shape[0])) {
                        throw std::runtime_error("ms1_expand_equal_fidelity_island_intensity_f64 full stream underflow");
                    }
                    std::copy(full_ptr + full_pos, full_ptr + full_pos + curr_len, curr_codes->begin());
                    full_pos += curr_len;
                } else if (int_kind == INT_KIND_DELTA2_V) {
                    if (history.size() < 2U) {
                        throw std::runtime_error("ms1_expand_equal_fidelity_island_intensity_f64 delta2 missing history");
                    }
                    if (delta_pos + curr_len > static_cast<std::size_t>(delta_buf.shape[0])) {
                        throw std::runtime_error("ms1_expand_equal_fidelity_island_intensity_f64 delta2 stream underflow");
                    }
                    const auto& prev1 = history[history.size() - 1U];
                    const auto& prev2 = history[history.size() - 2U];
                    for (std::size_t i = 0; i < curr_len; ++i) {
                        (*curr_codes)[i] = delta_ptr[delta_pos + i] + (2 * code_at(prev1, i)) - code_at(prev2, i);
                    }
                    delta_pos += curr_len;
                } else if (int_kind == INT_KIND_DELTA_V) {
                    const std::uint8_t ref_offset = delta_ref_pos < static_cast<std::size_t>(ref_buf.shape[0])
                        ? ref_ptr[delta_ref_pos]
                        : static_cast<std::uint8_t>(1);
                    ++delta_ref_pos;
                    std::shared_ptr<std::vector<std::int64_t>> ref_codes;
                    if (ref_offset == 0) {
                        if (cross_ref_pos >= static_cast<std::size_t>(cross_buf.shape[0])) {
                            throw std::runtime_error("ms1_expand_equal_fidelity_island_intensity_f64 missing cross-track ref");
                        }
                        const auto ref_idx = static_cast<std::size_t>(cross_ptr[cross_ref_pos++]);
                        auto it = cross_history.find(ref_idx);
                        if (it == cross_history.end()) {
                            throw std::runtime_error("ms1_expand_equal_fidelity_island_intensity_f64 invalid cross-track ref");
                        }
                        ref_codes = it->second;
                    } else {
                        if (ref_offset > history.size()) {
                            throw std::runtime_error("ms1_expand_equal_fidelity_island_intensity_f64 invalid in-track ref");
                        }
                        ref_codes = history[history.size() - ref_offset];
                    }
                    const auto ref_len = ref_codes ? ref_codes->size() : 0U;
                    const auto encoded_len = use_max_ref_curr ? std::max(curr_len, ref_len) : curr_len;
                    if (delta_pos + encoded_len > static_cast<std::size_t>(delta_buf.shape[0])) {
                        throw std::runtime_error("ms1_expand_equal_fidelity_island_intensity_f64 delta stream underflow");
                    }
                    for (std::size_t i = 0; i < curr_len; ++i) {
                        (*curr_codes)[i] = code_at(ref_codes, i) + delta_ptr[delta_pos + i];
                    }
                    delta_pos += encoded_len;
                } else {
                    throw std::runtime_error("ms1_expand_equal_fidelity_island_intensity_f64 unsupported int_kind");
                }
                auto* target = out_ptr + static_cast<std::size_t>(offset_ptr[island_pos]);
                for (std::size_t i = 0; i < curr_len; ++i) {
                    target[i] = decode_code((*curr_codes)[i]);
                }
                history.push_back(curr_codes);
                if (history.size() > history_limit) {
                    history.erase(history.begin(), history.begin() + static_cast<std::ptrdiff_t>(history.size() - history_limit));
                }
                if (needed_cross_refs.find(island_pos) != needed_cross_refs.end()) {
                    cross_history[island_pos] = curr_codes;
                }
                ++island_pos;
            }
        }
    }
    if (island_pos != island_count) {
        throw std::runtime_error("ms1_expand_equal_fidelity_island_intensity_f64 island count mismatch");
    }
    if (full_pos != static_cast<std::size_t>(full_buf.shape[0])) {
        throw std::runtime_error("ms1_expand_equal_fidelity_island_intensity_f64 full stream mismatch");
    }
    if (delta_pos != static_cast<std::size_t>(delta_buf.shape[0])) {
        throw std::runtime_error("ms1_expand_equal_fidelity_island_intensity_f64 delta stream mismatch");
    }
    return py::make_tuple(offsets, flat_intensity);
}

py::tuple ms1_expand_float64_island_intensity_f64(
    py::array_t<std::uint32_t, py::array::c_style | py::array::forcecast> track_lengths,
    py::array_t<std::uint32_t, py::array::c_style | py::array::forcecast> n_points,
    py::array_t<std::uint8_t, py::array::c_style | py::array::forcecast> int_kinds,
    py::array_t<std::uint8_t, py::array::c_style | py::array::forcecast> delta_ref_offsets,
    py::array_t<std::uint32_t, py::array::c_style | py::array::forcecast> cross_track_ref_indices,
    py::array_t<double, py::array::c_style | py::array::forcecast> full_values,
    py::array_t<double, py::array::c_style | py::array::forcecast> delta_values,
    bool use_max_ref_curr,
    bool enable_second_order_delta,
    int delta_ref_window
) {
    auto track_buf = track_lengths.request();
    auto npts_buf = n_points.request();
    auto kind_buf = int_kinds.request();
    auto ref_buf = delta_ref_offsets.request();
    auto cross_buf = cross_track_ref_indices.request();
    auto full_buf = full_values.request();
    auto delta_buf = delta_values.request();
    if (
        track_buf.ndim != 1 || npts_buf.ndim != 1 || kind_buf.ndim != 1 ||
        ref_buf.ndim != 1 || cross_buf.ndim != 1 || full_buf.ndim != 1 ||
        delta_buf.ndim != 1
    ) {
        throw std::runtime_error("ms1_expand_float64_island_intensity_f64 expects 1D arrays");
    }
    const auto island_count = static_cast<std::size_t>(npts_buf.shape[0]);
    if (static_cast<std::size_t>(kind_buf.shape[0]) != island_count) {
        throw std::runtime_error("ms1_expand_float64_island_intensity_f64 island metadata length mismatch");
    }
    const auto* track_ptr = static_cast<const std::uint32_t*>(track_buf.ptr);
    const auto* npts_ptr = static_cast<const std::uint32_t*>(npts_buf.ptr);
    const auto* kind_ptr = static_cast<const std::uint8_t*>(kind_buf.ptr);
    const auto* ref_ptr = static_cast<const std::uint8_t*>(ref_buf.ptr);
    const auto* cross_ptr = static_cast<const std::uint32_t*>(cross_buf.ptr);
    const auto* full_ptr = static_cast<const double*>(full_buf.ptr);
    const auto* delta_ptr = static_cast<const double*>(delta_buf.ptr);

    auto offsets = py::array_t<std::uint64_t>(island_count + 1U);
    auto* offset_ptr = offsets.mutable_data();
    offset_ptr[0] = 0U;
    for (std::size_t i = 0; i < island_count; ++i) {
        offset_ptr[i + 1U] = offset_ptr[i] + static_cast<std::uint64_t>(npts_ptr[i]);
    }
    const auto total_values = static_cast<std::size_t>(offset_ptr[island_count]);
    auto flat_intensity = py::array_t<double>(total_values);
    auto* out_ptr = flat_intensity.mutable_data();

    constexpr std::uint8_t INT_KIND_FULL_V = 0;
    constexpr std::uint8_t INT_KIND_DELTA_V = 1;
    constexpr std::uint8_t INT_KIND_DELTA2_V = 2;

    const std::size_t track_count = static_cast<std::size_t>(track_buf.shape[0]);
    const std::size_t history_limit = static_cast<std::size_t>(std::max(delta_ref_window, enable_second_order_delta ? 2 : 1));
    auto value_at = [](const double* ptr, std::size_t len, std::size_t pos) -> double {
        return pos < len ? ptr[pos] : 0.0;
    };

    std::size_t island_pos = 0;
    std::size_t full_pos = 0;
    std::size_t delta_pos = 0;
    std::size_t delta_ref_pos = 0;
    std::size_t cross_ref_pos = 0;

    {
        py::gil_scoped_release release;
        for (std::size_t track_idx = 0; track_idx < track_count; ++track_idx) {
            std::vector<std::uint32_t> history;
            history.reserve(history_limit);
            const auto track_len = static_cast<std::size_t>(track_ptr[track_idx]);
            for (std::size_t local = 0; local < track_len; ++local) {
                if (island_pos >= island_count) {
                    throw std::runtime_error("ms1_expand_float64_island_intensity_f64 track length exceeds island count");
                }
                const auto curr_len = static_cast<std::size_t>(npts_ptr[island_pos]);
                auto* target = out_ptr + static_cast<std::size_t>(offset_ptr[island_pos]);
                const auto int_kind = kind_ptr[island_pos];
                if (int_kind == INT_KIND_FULL_V) {
                    if (full_pos + curr_len > static_cast<std::size_t>(full_buf.shape[0])) {
                        throw std::runtime_error("ms1_expand_float64_island_intensity_f64 full stream underflow");
                    }
                    std::copy(full_ptr + full_pos, full_ptr + full_pos + curr_len, target);
                    full_pos += curr_len;
                } else if (int_kind == INT_KIND_DELTA2_V) {
                    if (history.size() < 2U) {
                        throw std::runtime_error("ms1_expand_float64_island_intensity_f64 delta2 missing history");
                    }
                    if (delta_pos + curr_len > static_cast<std::size_t>(delta_buf.shape[0])) {
                        throw std::runtime_error("ms1_expand_float64_island_intensity_f64 delta2 stream underflow");
                    }
                    const auto prev1_idx = static_cast<std::size_t>(history[history.size() - 1U]);
                    const auto prev2_idx = static_cast<std::size_t>(history[history.size() - 2U]);
                    const auto* prev1 = out_ptr + static_cast<std::size_t>(offset_ptr[prev1_idx]);
                    const auto* prev2 = out_ptr + static_cast<std::size_t>(offset_ptr[prev2_idx]);
                    const auto prev1_len = static_cast<std::size_t>(npts_ptr[prev1_idx]);
                    const auto prev2_len = static_cast<std::size_t>(npts_ptr[prev2_idx]);
                    for (std::size_t i = 0; i < curr_len; ++i) {
                        target[i] = delta_ptr[delta_pos + i] + (2.0 * value_at(prev1, prev1_len, i)) - value_at(prev2, prev2_len, i);
                    }
                    delta_pos += curr_len;
                } else if (int_kind == INT_KIND_DELTA_V) {
                    const std::uint8_t ref_offset = delta_ref_pos < static_cast<std::size_t>(ref_buf.shape[0])
                        ? ref_ptr[delta_ref_pos]
                        : static_cast<std::uint8_t>(1);
                    ++delta_ref_pos;
                    std::size_t ref_idx = 0;
                    if (ref_offset == 0) {
                        if (cross_ref_pos >= static_cast<std::size_t>(cross_buf.shape[0])) {
                            throw std::runtime_error("ms1_expand_float64_island_intensity_f64 missing cross-track ref");
                        }
                        ref_idx = static_cast<std::size_t>(cross_ptr[cross_ref_pos++]);
                        if (ref_idx >= island_pos) {
                            throw std::runtime_error("ms1_expand_float64_island_intensity_f64 invalid cross-track ref");
                        }
                    } else {
                        if (ref_offset > history.size()) {
                            throw std::runtime_error("ms1_expand_float64_island_intensity_f64 invalid in-track ref");
                        }
                        ref_idx = static_cast<std::size_t>(history[history.size() - ref_offset]);
                    }
                    if (ref_idx >= island_count) {
                        throw std::runtime_error("ms1_expand_float64_island_intensity_f64 ref index out of range");
                    }
                    const auto* ref = out_ptr + static_cast<std::size_t>(offset_ptr[ref_idx]);
                    const auto ref_len = static_cast<std::size_t>(npts_ptr[ref_idx]);
                    const auto encoded_len = use_max_ref_curr ? std::max(curr_len, ref_len) : curr_len;
                    if (delta_pos + encoded_len > static_cast<std::size_t>(delta_buf.shape[0])) {
                        throw std::runtime_error("ms1_expand_float64_island_intensity_f64 delta stream underflow");
                    }
                    for (std::size_t i = 0; i < curr_len; ++i) {
                        target[i] = value_at(ref, ref_len, i) + delta_ptr[delta_pos + i];
                    }
                    delta_pos += encoded_len;
                } else {
                    throw std::runtime_error("ms1_expand_float64_island_intensity_f64 unsupported int_kind");
                }
                history.push_back(static_cast<std::uint32_t>(island_pos));
                if (history.size() > history_limit) {
                    history.erase(history.begin(), history.begin() + static_cast<std::ptrdiff_t>(history.size() - history_limit));
                }
                ++island_pos;
            }
        }
    }
    if (island_pos != island_count) {
        throw std::runtime_error("ms1_expand_float64_island_intensity_f64 island count mismatch");
    }
    if (full_pos != static_cast<std::size_t>(full_buf.shape[0])) {
        throw std::runtime_error("ms1_expand_float64_island_intensity_f64 full stream mismatch");
    }
    if (delta_pos != static_cast<std::size_t>(delta_buf.shape[0])) {
        throw std::runtime_error("ms1_expand_float64_island_intensity_f64 delta stream mismatch");
    }
    return py::make_tuple(offsets, flat_intensity);
}

py::dict best_delta_reference_equal_fidelity(
    py::array_t<double, py::array::c_style | py::array::forcecast> curr,
    py::list history_segments,
    int precision,
    int delta_ref_window,
    int padded_delta_max_diff,
    bool use_max_ref_curr,
    py::object full_est_obj = py::none()
) {
    auto curr_buf = curr.request();
    const auto curr_len = static_cast<std::size_t>(curr_buf.size);
    const auto* curr_ptr = static_cast<const double*>(curr_buf.ptr);
    const double precision_d = static_cast<double>(precision);
    const int full_est = full_est_obj.is_none()
        ? static_cast<int>(estimate_scaled_full_bytes_core(curr_ptr, curr_len, precision_d))
        : py::cast<int>(full_est_obj);

    const std::size_t history_size = static_cast<std::size_t>(py::len(history_segments));
    const std::size_t lookback = std::min<std::size_t>(
        history_size,
        static_cast<std::size_t>(delta_ref_window < 0 ? 0 : delta_ref_window)
    );
    std::vector<py::array_t<double, py::array::c_style | py::array::forcecast>> history_arrays;
    history_arrays.reserve(history_size);
    std::vector<py::buffer_info> history_bufs;
    history_bufs.reserve(history_size);
    for (std::size_t i = 0; i < history_size; ++i) {
        auto arr = history_segments[i].cast<py::array_t<double, py::array::c_style | py::array::forcecast>>();
        history_bufs.push_back(arr.request());
        history_arrays.push_back(arr);
    }

    bool found = false;
    int best_offset = 0;
    int best_delta_est = -1;
    int best_encoded_len = -1;
    int best_length_diff = 0;
    double best_mean_abs = 0.0;

    {
        py::gil_scoped_release release;
        for (std::size_t ref_offset = 1; ref_offset <= lookback; ++ref_offset) {
            const auto ref_idx = history_size - ref_offset;
            const auto ref_len = static_cast<std::size_t>(history_bufs[ref_idx].size);
            const auto* ref_ptr = static_cast<const double*>(history_bufs[ref_idx].ptr);
            const int length_diff = static_cast<int>(curr_len > ref_len ? curr_len - ref_len : ref_len - curr_len);
            if (length_diff > padded_delta_max_diff) {
                continue;
            }
            const std::size_t encoded_len = use_max_ref_curr ? std::max(curr_len, ref_len) : curr_len;
            const int delta_est = static_cast<int>(
                estimate_scaled_delta_bytes_core(curr_ptr, curr_len, ref_ptr, ref_len, encoded_len, precision_d)
            );
            if (delta_est >= full_est) {
                continue;
            }
            const double mean_abs = mean_abs_padded_delta(curr_ptr, curr_len, ref_ptr, ref_len, encoded_len);
            if (
                !found
                || delta_est < best_delta_est
                || (delta_est == best_delta_est && mean_abs < best_mean_abs)
                || (delta_est == best_delta_est && mean_abs == best_mean_abs && length_diff < best_length_diff)
                || (
                    delta_est == best_delta_est
                    && mean_abs == best_mean_abs
                    && length_diff == best_length_diff
                    && static_cast<int>(ref_offset) < best_offset
                )
            ) {
                found = true;
                best_offset = static_cast<int>(ref_offset);
                best_delta_est = delta_est;
                best_encoded_len = static_cast<int>(encoded_len);
                best_length_diff = length_diff;
                best_mean_abs = mean_abs;
            }
        }
    }

    py::dict out;
    out["offset"] = py::int_(best_offset);
    out["full_est"] = py::int_(full_est);
    out["delta_est"] = found ? py::object(py::int_(best_delta_est)) : py::object(py::none());
    out["encoded_len"] = found ? py::object(py::int_(best_encoded_len)) : py::object(py::none());
    return out;
}

py::dict best_cross_track_reference_equal_fidelity(
    py::array_t<double, py::array::c_style | py::array::forcecast> curr,
    py::list candidate_segments,
    py::array_t<std::uint32_t, py::array::c_style | py::array::forcecast> candidate_indices,
    int precision,
    int padded_delta_max_diff,
    bool use_max_ref_curr,
    int candidate_limit,
    int cross_track_min_gain_bytes,
    py::object full_est_obj = py::none()
) {
    auto curr_buf = curr.request();
    const auto curr_len = static_cast<std::size_t>(curr_buf.size);
    const auto* curr_ptr = static_cast<const double*>(curr_buf.ptr);
    const double precision_d = static_cast<double>(precision);
    const int full_est = full_est_obj.is_none()
        ? static_cast<int>(estimate_scaled_full_bytes_core(curr_ptr, curr_len, precision_d))
        : py::cast<int>(full_est_obj);

    auto idx_buf = candidate_indices.request();
    const auto candidate_count = static_cast<std::size_t>(py::len(candidate_segments));
    if (candidate_count != static_cast<std::size_t>(idx_buf.size)) {
        throw std::runtime_error("best_cross_track_reference_equal_fidelity candidate length mismatch");
    }
    const auto* ref_index_ptr = static_cast<const std::uint32_t*>(idx_buf.ptr);
    std::vector<py::array_t<double, py::array::c_style | py::array::forcecast>> candidate_arrays;
    candidate_arrays.reserve(candidate_count);
    std::vector<py::buffer_info> candidate_bufs;
    candidate_bufs.reserve(candidate_count);
    for (std::size_t pos = 0; pos < candidate_count; ++pos) {
        auto arr = candidate_segments[pos].cast<py::array_t<double, py::array::c_style | py::array::forcecast>>();
        candidate_bufs.push_back(arr.request());
        candidate_arrays.push_back(arr);
    }

    struct CandidateMeta {
        std::size_t pos;
        std::size_t len;
        int len_diff;
        std::uint32_t ref_index;
    };

    std::vector<CandidateMeta> filtered;
    filtered.reserve(candidate_count);
    for (std::size_t pos = 0; pos < candidate_count; ++pos) {
        const auto ref_len = static_cast<std::size_t>(candidate_bufs[pos].size);
        const int len_diff = static_cast<int>(curr_len > ref_len ? curr_len - ref_len : ref_len - curr_len);
        if (len_diff > padded_delta_max_diff) {
            continue;
        }
        filtered.push_back(CandidateMeta{pos, ref_len, len_diff, ref_index_ptr[pos]});
    }

    if (candidate_limit > 0 && filtered.size() > static_cast<std::size_t>(candidate_limit)) {
        std::sort(
            filtered.begin(),
            filtered.end(),
            [](const CandidateMeta& a, const CandidateMeta& b) {
                if (a.len_diff != b.len_diff) {
                    return a.len_diff < b.len_diff;
                }
                return a.ref_index > b.ref_index;
            }
        );
        filtered.resize(static_cast<std::size_t>(candidate_limit));
    }

    bool found = false;
    std::size_t best_pos = 0;
    int best_ref_index = -1;
    int best_delta_est = -1;
    int best_encoded_len = -1;
    int best_length_diff = 0;

    {
        py::gil_scoped_release release;
        for (const auto& item : filtered) {
            const auto* ref_ptr = static_cast<const double*>(candidate_bufs[item.pos].ptr);
            const std::size_t encoded_len = use_max_ref_curr ? std::max(curr_len, item.len) : curr_len;
            const int delta_est = static_cast<int>(
                estimate_scaled_delta_bytes_core(curr_ptr, curr_len, ref_ptr, item.len, encoded_len, precision_d)
            );
            if (delta_est + cross_track_min_gain_bytes >= full_est) {
                continue;
            }
            if (
                !found
                || delta_est < best_delta_est
                || (delta_est == best_delta_est && item.len_diff < best_length_diff)
                || (delta_est == best_delta_est && item.len_diff == best_length_diff && static_cast<int>(item.ref_index) > best_ref_index)
            ) {
                found = true;
                best_pos = item.pos;
                best_ref_index = static_cast<int>(item.ref_index);
                best_delta_est = delta_est;
                best_encoded_len = static_cast<int>(encoded_len);
                best_length_diff = item.len_diff;
            }
        }
    }

    py::dict out;
    out["candidate_pos"] = found ? py::object(py::int_(static_cast<int>(best_pos))) : py::object(py::none());
    out["ref_index"] = found ? py::object(py::int_(best_ref_index)) : py::object(py::none());
    out["full_est"] = py::int_(full_est);
    out["delta_est"] = found ? py::object(py::int_(best_delta_est)) : py::object(py::none());
    out["encoded_len"] = found ? py::object(py::int_(best_encoded_len)) : py::object(py::none());
    return out;
}

py::tuple encode_signed_int64_to_int32_main(py::array_t<std::int64_t, py::array::c_style | py::array::forcecast> arr) {
    auto buf = arr.request();
    const auto count = static_cast<std::size_t>(buf.size);
    auto main = py::array_t<std::int32_t>(count);
    auto main_buf = main.request();
    auto* main_ptr = static_cast<std::int32_t*>(main_buf.ptr);
    const auto* data = static_cast<const std::int64_t*>(buf.ptr);

    constexpr std::int64_t INT32_MIN_LL = static_cast<std::int64_t>(INT32_MIN);
    constexpr std::int64_t INT32_MAX_LL = static_cast<std::int64_t>(INT32_MAX);

    std::vector<std::uint32_t> overflow_idx;
    std::vector<std::int64_t> overflow_vals;
    overflow_idx.reserve(128);
    overflow_vals.reserve(128);

    {
        py::gil_scoped_release release;
        for (std::size_t i = 0; i < count; ++i) {
            const std::int64_t value = data[i];
            if (value < INT32_MIN_LL || value > INT32_MAX_LL) {
                main_ptr[i] = 0;
                overflow_idx.push_back(static_cast<std::uint32_t>(i));
                overflow_vals.push_back(value);
            } else {
                main_ptr[i] = static_cast<std::int32_t>(value);
            }
        }
    }

    auto idx = py::array_t<std::uint32_t>(overflow_idx.size());
    auto idx_buf = idx.request();
    auto* idx_ptr = static_cast<std::uint32_t*>(idx_buf.ptr);
    for (std::size_t i = 0; i < overflow_idx.size(); ++i) {
        idx_ptr[i] = overflow_idx[i];
    }

    auto vals = py::array_t<std::int64_t>(overflow_vals.size());
    auto vals_buf = vals.request();
    auto* vals_ptr = static_cast<std::int64_t*>(vals_buf.ptr);
    for (std::size_t i = 0; i < overflow_vals.size(); ++i) {
        vals_ptr[i] = overflow_vals[i];
    }

    return py::make_tuple(main, idx, vals);
}

py::tuple ms2_exact_track_collect_entries(
    py::array_t<double, py::array::c_style | py::array::forcecast> mz_data,
    py::array_t<double, py::array::c_style | py::array::forcecast> intensity_data,
    py::array_t<std::uint64_t, py::array::c_style | py::array::forcecast> offsets,
    py::array_t<std::uint32_t, py::array::c_style | py::array::forcecast> lengths,
    double mz_scale
) {
    if (mz_scale <= 0.0) {
        throw std::runtime_error("ms2_exact_track_collect_entries mz_scale must be positive");
    }
    auto mz_buf = mz_data.request();
    auto int_buf = intensity_data.request();
    auto off_buf = offsets.request();
    auto len_buf = lengths.request();
    if (mz_buf.ndim != 1 || int_buf.ndim != 1 || off_buf.ndim != 1 || len_buf.ndim != 1) {
        throw std::runtime_error("ms2_exact_track_collect_entries expects 1D arrays");
    }
    if (mz_buf.shape[0] != int_buf.shape[0]) {
        throw std::runtime_error("ms2_exact_track_collect_entries mz/intensity length mismatch");
    }
    if (off_buf.shape[0] != len_buf.shape[0]) {
        throw std::runtime_error("ms2_exact_track_collect_entries offset/length mismatch");
    }

    const auto total_data_points = static_cast<std::size_t>(mz_buf.shape[0]);
    const auto n_scans = static_cast<std::size_t>(len_buf.shape[0]);
    const auto* mz_ptr = static_cast<const double*>(mz_buf.ptr);
    const auto* int_ptr = static_cast<const double*>(int_buf.ptr);
    const auto* off_ptr = static_cast<const std::uint64_t*>(off_buf.ptr);
    const auto* len_ptr = static_cast<const std::uint32_t*>(len_buf.ptr);

    std::size_t point_count = 0;
    for (std::size_t scan = 0; scan < n_scans; ++scan) {
        const auto offset = static_cast<std::size_t>(off_ptr[scan]);
        const auto length = static_cast<std::size_t>(len_ptr[scan]);
        if (offset + length > total_data_points) {
            throw std::runtime_error("ms2_exact_track_collect_entries scan slice out of range");
        }
        point_count += length;
    }

    auto mz_q = py::array_t<std::int64_t>(point_count);
    auto intensities = py::array_t<double>(point_count);
    auto scan_indices = py::array_t<std::uint32_t>(point_count);
    auto* mz_out = mz_q.mutable_data();
    auto* intensity_out = intensities.mutable_data();
    auto* scan_out = scan_indices.mutable_data();

    {
        py::gil_scoped_release release;
        std::size_t out_pos = 0;
        for (std::size_t scan = 0; scan < n_scans; ++scan) {
            const auto offset = static_cast<std::size_t>(off_ptr[scan]);
            const auto length = static_cast<std::size_t>(len_ptr[scan]);
            for (std::size_t i = 0; i < length; ++i) {
                const auto pos = offset + i;
                mz_out[out_pos] = static_cast<std::int64_t>(std::llrint(mz_ptr[pos] * mz_scale));
                intensity_out[out_pos] = int_ptr[pos];
                scan_out[out_pos] = static_cast<std::uint32_t>(scan);
                ++out_pos;
            }
        }
    }

    return py::make_tuple(mz_q, intensities, scan_indices);
}

py::tuple ms2_exact_track_build_follow_deltas(
    py::array_t<std::uint32_t, py::array::c_style | py::array::forcecast> track_lengths,
    py::array_t<std::int64_t, py::array::c_style | py::array::forcecast> flat_scan_idx,
    py::array_t<std::int64_t, py::array::c_style | py::array::forcecast> codes_concat64
) {
    auto len_buf = track_lengths.request();
    auto scan_buf = flat_scan_idx.request();
    auto code_buf = codes_concat64.request();
    if (len_buf.ndim != 1 || scan_buf.ndim != 1 || code_buf.ndim != 1) {
        throw std::runtime_error("ms2_exact_track_build_follow_deltas expects 1D arrays");
    }
    const auto n_tracks = static_cast<std::size_t>(len_buf.shape[0]);
    const auto* lengths = static_cast<const std::uint32_t*>(len_buf.ptr);
    const auto* scans = static_cast<const std::int64_t*>(scan_buf.ptr);
    const auto* codes = static_cast<const std::int64_t*>(code_buf.ptr);
    std::size_t total_points = 0;
    std::size_t follow_points = 0;
    for (std::size_t i = 0; i < n_tracks; ++i) {
        total_points += static_cast<std::size_t>(lengths[i]);
        if (lengths[i] > 1U) {
            follow_points += static_cast<std::size_t>(lengths[i] - 1U);
        }
    }
    if (
        static_cast<std::size_t>(scan_buf.shape[0]) != total_points ||
        static_cast<std::size_t>(code_buf.shape[0]) != total_points
    ) {
        throw std::runtime_error("ms2_exact_track_build_follow_deltas input length mismatch");
    }

    auto scan_gaps = py::array_t<std::uint32_t>(follow_points);
    auto code_deltas = py::array_t<std::int64_t>(follow_points);
    auto* gap_out = scan_gaps.mutable_data();
    auto* delta_out = code_deltas.mutable_data();
    {
        py::gil_scoped_release release;
        std::size_t point_pos = 0;
        std::size_t out_pos = 0;
        for (std::size_t track = 0; track < n_tracks; ++track) {
            const std::size_t length = static_cast<std::size_t>(lengths[track]);
            if (length == 0U) {
                continue;
            }
            for (std::size_t i = 1; i < length; ++i) {
                const std::int64_t scan_delta = scans[point_pos + i] - scans[point_pos + i - 1U];
                if (scan_delta < 0 || scan_delta > static_cast<std::int64_t>(std::numeric_limits<std::uint32_t>::max())) {
                    throw std::runtime_error("ms2 exact-track scan delta out of uint32 range");
                }
                gap_out[out_pos] = static_cast<std::uint32_t>(scan_delta);
                delta_out[out_pos] = codes[point_pos + i] - codes[point_pos + i - 1U];
                ++out_pos;
            }
            point_pos += length;
        }
    }
    return py::make_tuple(scan_gaps, code_deltas);
}

py::array_t<std::uint32_t> ms2_select_top_ids(
    py::array_t<std::uint32_t, py::array::c_style | py::array::forcecast> track_lengths,
    py::array_t<std::int64_t, py::array::c_style | py::array::forcecast> starts,
    py::array_t<std::int64_t, py::array::c_style | py::array::forcecast> flat_scan_idx,
    py::array_t<double, py::array::c_style | py::array::forcecast> joined_intensity,
    int top_k,
    int min_track_length
) {
    auto len_buf = track_lengths.request();
    auto start_buf = starts.request();
    auto scan_buf = flat_scan_idx.request();
    auto int_buf = joined_intensity.request();
    if (len_buf.ndim != 1 || start_buf.ndim != 1 || scan_buf.ndim != 1 || int_buf.ndim != 1) {
        throw std::runtime_error("ms2_select_top_ids expects 1D arrays");
    }
    const auto n_tracks = static_cast<std::size_t>(len_buf.shape[0]);
    if (static_cast<std::size_t>(start_buf.shape[0]) != n_tracks) {
        throw std::runtime_error("ms2_select_top_ids track array length mismatch");
    }
    if (static_cast<std::size_t>(scan_buf.shape[0]) != static_cast<std::size_t>(int_buf.shape[0])) {
        throw std::runtime_error("ms2_select_top_ids point array length mismatch");
    }
    if (top_k <= 0 || n_tracks == 0U) {
        return py::array_t<std::uint32_t>(0);
    }
    const auto* lengths = static_cast<const std::uint32_t*>(len_buf.ptr);
    const auto* start_values = static_cast<const std::int64_t*>(start_buf.ptr);
    const auto* scans = static_cast<const std::int64_t*>(scan_buf.ptr);
    const auto* intensities = static_cast<const double*>(int_buf.ptr);
    const auto point_count = static_cast<std::size_t>(scan_buf.shape[0]);

    struct Candidate {
        std::uint32_t track_id;
        std::uint32_t length;
        double total_intensity;
    };
    std::vector<Candidate> candidates;
    candidates.reserve(std::min<std::size_t>(n_tracks, static_cast<std::size_t>(std::max(0, top_k)) * 4U + 64U));
    {
        py::gil_scoped_release release;
        for (std::size_t track = 0; track < n_tracks; ++track) {
            const std::size_t length = static_cast<std::size_t>(lengths[track]);
            if (length < static_cast<std::size_t>(std::max(0, min_track_length))) {
                continue;
            }
            const auto start_i64 = start_values[track];
            if (start_i64 < 0) {
                throw std::runtime_error("ms2_select_top_ids negative track start");
            }
            const std::size_t start = static_cast<std::size_t>(start_i64);
            if (start + length > point_count) {
                throw std::runtime_error("ms2_select_top_ids track slice out of range");
            }
            bool duplicate_scan = false;
            double total = 0.0;
            std::int64_t prev_scan = std::numeric_limits<std::int64_t>::min();
            for (std::size_t i = 0; i < length; ++i) {
                const std::int64_t scan = scans[start + i];
                if (i > 0U && scan == prev_scan) {
                    duplicate_scan = true;
                    break;
                }
                prev_scan = scan;
                total += intensities[start + i];
            }
            if (!duplicate_scan) {
                candidates.push_back(Candidate{
                    static_cast<std::uint32_t>(track),
                    static_cast<std::uint32_t>(length),
                    total,
                });
            }
        }
        std::sort(candidates.begin(), candidates.end(), [](const Candidate& a, const Candidate& b) {
            if (a.length != b.length) {
                return a.length > b.length;
            }
            if (a.total_intensity != b.total_intensity) {
                return a.total_intensity > b.total_intensity;
            }
            return a.track_id < b.track_id;
        });
    }
    const std::size_t out_count = std::min<std::size_t>(candidates.size(), static_cast<std::size_t>(top_k));
    auto out = py::array_t<std::uint32_t>(out_count);
    auto* out_ptr = out.mutable_data();
    for (std::size_t i = 0; i < out_count; ++i) {
        out_ptr[i] = candidates[i].track_id;
    }
    return out;
}

py::tuple ms2_exact_track_expand_by_scan(
    int n_scans,
    py::array_t<std::int64_t, py::array::c_style | py::array::forcecast> dict_mz_q,
    py::array_t<std::uint32_t, py::array::c_style | py::array::forcecast> track_lengths,
    py::array_t<std::uint32_t, py::array::c_style | py::array::forcecast> scan_firsts,
    py::array_t<std::uint32_t, py::array::c_style | py::array::forcecast> scan_gaps,
    py::array_t<std::int64_t, py::array::c_style | py::array::forcecast> int_first_codes64,
    py::array_t<std::int64_t, py::array::c_style | py::array::forcecast> int_delta_main,
    bool gap_minus_one
) {
    if (n_scans < 0) {
        throw std::runtime_error("ms2_exact_track_expand_by_scan n_scans must be non-negative");
    }
    auto mz_buf = dict_mz_q.request();
    auto len_buf = track_lengths.request();
    auto first_buf = scan_firsts.request();
    auto gap_buf = scan_gaps.request();
    auto first_code_buf = int_first_codes64.request();
    auto delta_buf = int_delta_main.request();
    if (
        mz_buf.ndim != 1 || len_buf.ndim != 1 || first_buf.ndim != 1 ||
        gap_buf.ndim != 1 || first_code_buf.ndim != 1 || delta_buf.ndim != 1
    ) {
        throw std::runtime_error("ms2_exact_track_expand_by_scan expects 1D arrays");
    }
    const auto n_tracks = static_cast<std::size_t>(len_buf.shape[0]);
    if (
        static_cast<std::size_t>(mz_buf.shape[0]) != n_tracks ||
        static_cast<std::size_t>(first_buf.shape[0]) != n_tracks ||
        static_cast<std::size_t>(first_code_buf.shape[0]) != n_tracks
    ) {
        throw std::runtime_error("ms2_exact_track_expand_by_scan track array length mismatch");
    }
    const auto* mz_values = static_cast<const std::int64_t*>(mz_buf.ptr);
    const auto* lengths = static_cast<const std::uint32_t*>(len_buf.ptr);
    const auto* firsts = static_cast<const std::uint32_t*>(first_buf.ptr);
    const auto* gaps = static_cast<const std::uint32_t*>(gap_buf.ptr);
    const auto* first_codes = static_cast<const std::int64_t*>(first_code_buf.ptr);
    const auto* deltas = static_cast<const std::int64_t*>(delta_buf.ptr);

    std::vector<std::uint64_t> counts(static_cast<std::size_t>(n_scans), 0U);
    std::size_t expected_follow = 0;
    std::size_t total_points = 0;
    {
        py::gil_scoped_release release;
        std::size_t gap_pos = 0;
        for (std::size_t track = 0; track < n_tracks; ++track) {
            const std::size_t length = static_cast<std::size_t>(lengths[track]);
            total_points += length;
            if (length == 0U) {
                continue;
            }
            expected_follow += length - 1U;
            std::int64_t scan_idx = static_cast<std::int64_t>(firsts[track]);
            if (scan_idx < 0 || scan_idx >= static_cast<std::int64_t>(n_scans)) {
                throw std::runtime_error("ms2 exact-track first scan index out of range");
            }
            counts[static_cast<std::size_t>(scan_idx)] += 1U;
            for (std::size_t i = 1; i < length; ++i) {
                if (gap_pos >= static_cast<std::size_t>(gap_buf.shape[0])) {
                    throw std::runtime_error("ms2 exact-track scan gap underflow");
                }
                std::int64_t step = static_cast<std::int64_t>(gaps[gap_pos++]);
                if (gap_minus_one) {
                    step += 1;
                }
                scan_idx += step;
                if (scan_idx < 0 || scan_idx >= static_cast<std::int64_t>(n_scans)) {
                    throw std::runtime_error("ms2 exact-track scan index out of range");
                }
                counts[static_cast<std::size_t>(scan_idx)] += 1U;
            }
        }
    }
    if (
        static_cast<std::size_t>(gap_buf.shape[0]) != expected_follow ||
        static_cast<std::size_t>(delta_buf.shape[0]) != expected_follow
    ) {
        throw std::runtime_error("ms2_exact_track_expand_by_scan follow array length mismatch");
    }

    auto offsets = py::array_t<std::uint64_t>(static_cast<std::size_t>(n_scans) + 1U);
    auto* offset_data = offsets.mutable_data();
    offset_data[0] = 0U;
    for (int i = 0; i < n_scans; ++i) {
        offset_data[static_cast<std::size_t>(i) + 1U] = offset_data[static_cast<std::size_t>(i)] + counts[static_cast<std::size_t>(i)];
    }
    if (offset_data[static_cast<std::size_t>(n_scans)] != static_cast<std::uint64_t>(total_points)) {
        throw std::runtime_error("ms2 exact-track point count mismatch");
    }

    auto mz_out = py::array_t<std::int64_t>(total_points);
    auto code_out = py::array_t<std::int64_t>(total_points);
    auto* mz_data = mz_out.mutable_data();
    auto* code_data = code_out.mutable_data();
    std::vector<std::uint64_t> write_pos(static_cast<std::size_t>(n_scans), 0U);
    for (int i = 0; i < n_scans; ++i) {
        write_pos[static_cast<std::size_t>(i)] = offset_data[static_cast<std::size_t>(i)];
    }
    {
        py::gil_scoped_release release;
        std::size_t gap_pos = 0;
        std::size_t delta_pos = 0;
        for (std::size_t track = 0; track < n_tracks; ++track) {
            const std::size_t length = static_cast<std::size_t>(lengths[track]);
            if (length == 0U) {
                continue;
            }
            std::int64_t scan_idx = static_cast<std::int64_t>(firsts[track]);
            std::int64_t code = first_codes[track];
            auto write_point = [&](std::int64_t target_scan, std::int64_t target_code) {
                const auto scan_u = static_cast<std::size_t>(target_scan);
                const auto out_pos = static_cast<std::size_t>(write_pos[scan_u]++);
                mz_data[out_pos] = mz_values[track];
                code_data[out_pos] = target_code;
            };
            write_point(scan_idx, code);
            for (std::size_t i = 1; i < length; ++i) {
                std::int64_t step = static_cast<std::int64_t>(gaps[gap_pos++]);
                if (gap_minus_one) {
                    step += 1;
                }
                scan_idx += step;
                code += deltas[delta_pos++];
                write_point(scan_idx, code);
            }
        }
    }
    return py::make_tuple(offsets, mz_out, code_out);
}

py::tuple ms2_exact_track_expand_by_scan_f64(
    int n_scans,
    py::array_t<std::int64_t, py::array::c_style | py::array::forcecast> dict_mz_q,
    py::array_t<std::uint32_t, py::array::c_style | py::array::forcecast> track_lengths,
    py::array_t<std::uint32_t, py::array::c_style | py::array::forcecast> scan_firsts,
    py::array_t<std::uint32_t, py::array::c_style | py::array::forcecast> scan_gaps,
    py::array_t<std::int64_t, py::array::c_style | py::array::forcecast> int_first_codes64,
    py::array_t<std::int64_t, py::array::c_style | py::array::forcecast> int_delta_main,
    bool gap_minus_one,
    double mz_scale,
    double intensity_precision,
    bool exact_scaled_intensity
) {
    if (n_scans < 0) {
        throw std::runtime_error("ms2_exact_track_expand_by_scan_f64 n_scans must be non-negative");
    }
    if (mz_scale == 0.0 || intensity_precision == 0.0) {
        throw std::runtime_error("ms2_exact_track_expand_by_scan_f64 scale values must be non-zero");
    }
    auto mz_buf = dict_mz_q.request();
    auto len_buf = track_lengths.request();
    auto first_buf = scan_firsts.request();
    auto gap_buf = scan_gaps.request();
    auto first_code_buf = int_first_codes64.request();
    auto delta_buf = int_delta_main.request();
    if (
        mz_buf.ndim != 1 || len_buf.ndim != 1 || first_buf.ndim != 1 ||
        gap_buf.ndim != 1 || first_code_buf.ndim != 1 || delta_buf.ndim != 1
    ) {
        throw std::runtime_error("ms2_exact_track_expand_by_scan_f64 expects 1D arrays");
    }
    const auto n_tracks = static_cast<std::size_t>(len_buf.shape[0]);
    if (
        static_cast<std::size_t>(mz_buf.shape[0]) != n_tracks ||
        static_cast<std::size_t>(first_buf.shape[0]) != n_tracks ||
        static_cast<std::size_t>(first_code_buf.shape[0]) != n_tracks
    ) {
        throw std::runtime_error("ms2_exact_track_expand_by_scan_f64 track array length mismatch");
    }
    const auto* mz_values = static_cast<const std::int64_t*>(mz_buf.ptr);
    const auto* lengths = static_cast<const std::uint32_t*>(len_buf.ptr);
    const auto* firsts = static_cast<const std::uint32_t*>(first_buf.ptr);
    const auto* gaps = static_cast<const std::uint32_t*>(gap_buf.ptr);
    const auto* first_codes = static_cast<const std::int64_t*>(first_code_buf.ptr);
    const auto* deltas = static_cast<const std::int64_t*>(delta_buf.ptr);

    std::vector<std::uint64_t> counts(static_cast<std::size_t>(n_scans), 0U);
    std::size_t expected_follow = 0;
    std::size_t total_points = 0;
    {
        py::gil_scoped_release release;
        std::size_t gap_pos = 0;
        for (std::size_t track = 0; track < n_tracks; ++track) {
            const std::size_t length = static_cast<std::size_t>(lengths[track]);
            total_points += length;
            if (length == 0U) {
                continue;
            }
            expected_follow += length - 1U;
            std::int64_t scan_idx = static_cast<std::int64_t>(firsts[track]);
            if (scan_idx < 0 || scan_idx >= static_cast<std::int64_t>(n_scans)) {
                throw std::runtime_error("ms2 exact-track f64 first scan index out of range");
            }
            counts[static_cast<std::size_t>(scan_idx)] += 1U;
            for (std::size_t i = 1; i < length; ++i) {
                if (gap_pos >= static_cast<std::size_t>(gap_buf.shape[0])) {
                    throw std::runtime_error("ms2 exact-track f64 scan gap underflow");
                }
                std::int64_t step = static_cast<std::int64_t>(gaps[gap_pos++]);
                if (gap_minus_one) {
                    step += 1;
                }
                scan_idx += step;
                if (scan_idx < 0 || scan_idx >= static_cast<std::int64_t>(n_scans)) {
                    throw std::runtime_error("ms2 exact-track f64 scan index out of range");
                }
                counts[static_cast<std::size_t>(scan_idx)] += 1U;
            }
        }
    }
    if (
        static_cast<std::size_t>(gap_buf.shape[0]) != expected_follow ||
        static_cast<std::size_t>(delta_buf.shape[0]) != expected_follow
    ) {
        throw std::runtime_error("ms2_exact_track_expand_by_scan_f64 follow array length mismatch");
    }

    auto offsets = py::array_t<std::uint64_t>(static_cast<std::size_t>(n_scans) + 1U);
    auto* offset_data = offsets.mutable_data();
    offset_data[0] = 0U;
    for (int i = 0; i < n_scans; ++i) {
        offset_data[static_cast<std::size_t>(i) + 1U] = offset_data[static_cast<std::size_t>(i)] + counts[static_cast<std::size_t>(i)];
    }
    if (offset_data[static_cast<std::size_t>(n_scans)] != static_cast<std::uint64_t>(total_points)) {
        throw std::runtime_error("ms2 exact-track f64 point count mismatch");
    }

    auto mz_out = py::array_t<double>(total_points);
    auto intensity_out = py::array_t<double>(total_points);
    auto* mz_data = mz_out.mutable_data();
    auto* intensity_data = intensity_out.mutable_data();
    std::vector<std::uint64_t> write_pos(static_cast<std::size_t>(n_scans), 0U);
    for (int i = 0; i < n_scans; ++i) {
        write_pos[static_cast<std::size_t>(i)] = offset_data[static_cast<std::size_t>(i)];
    }
    {
        py::gil_scoped_release release;
        std::size_t gap_pos = 0;
        std::size_t delta_pos = 0;
        for (std::size_t track = 0; track < n_tracks; ++track) {
            const std::size_t length = static_cast<std::size_t>(lengths[track]);
            if (length == 0U) {
                continue;
            }
            std::int64_t scan_idx = static_cast<std::int64_t>(firsts[track]);
            std::int64_t code = first_codes[track];
            const double mz_value = static_cast<double>(mz_values[track]) / mz_scale;
            auto decode_code = [&](std::int64_t target_code) -> double {
                if (exact_scaled_intensity || target_code >= 0) {
                    return static_cast<double>(target_code) / intensity_precision;
                }
                return std::pow(2.0, -static_cast<double>(target_code) / 100000.0) / intensity_precision;
            };
            auto write_point = [&](std::int64_t target_scan, std::int64_t target_code) {
                const auto scan_u = static_cast<std::size_t>(target_scan);
                const auto out_pos = static_cast<std::size_t>(write_pos[scan_u]++);
                mz_data[out_pos] = mz_value;
                intensity_data[out_pos] = decode_code(target_code);
            };
            write_point(scan_idx, code);
            for (std::size_t i = 1; i < length; ++i) {
                std::int64_t step = static_cast<std::int64_t>(gaps[gap_pos++]);
                if (gap_minus_one) {
                    step += 1;
                }
                scan_idx += step;
                code += deltas[delta_pos++];
                write_point(scan_idx, code);
            }
        }
        std::vector<std::pair<double, double>> sort_buffer;
        for (int scan = 0; scan < n_scans; ++scan) {
            const auto begin = static_cast<std::size_t>(offset_data[static_cast<std::size_t>(scan)]);
            const auto end = static_cast<std::size_t>(offset_data[static_cast<std::size_t>(scan) + 1U]);
            if (end <= begin + 1U) {
                continue;
            }
            bool sorted = true;
            for (std::size_t pos = begin + 1U; pos < end; ++pos) {
                if (mz_data[pos] < mz_data[pos - 1U]) {
                    sorted = false;
                    break;
                }
            }
            if (sorted) {
                continue;
            }
            const auto count = end - begin;
            sort_buffer.resize(count);
            for (std::size_t i = 0; i < count; ++i) {
                const auto pos = begin + i;
                sort_buffer[i] = std::make_pair(mz_data[pos], intensity_data[pos]);
            }
            std::stable_sort(
                sort_buffer.begin(),
                sort_buffer.end(),
                [](const auto& lhs, const auto& rhs) {
                    return lhs.first < rhs.first;
                }
            );
            for (std::size_t i = 0; i < count; ++i) {
                const auto pos = begin + i;
                mz_data[pos] = sort_buffer[i].first;
                intensity_data[pos] = sort_buffer[i].second;
            }
        }
    }
    return py::make_tuple(offsets, mz_out, intensity_out);
}

py::tuple ms1_collect_full_mz_qdelta(
    py::array_t<double, py::array::c_style | py::array::forcecast> mz_data,
    py::array_t<std::uint64_t, py::array::c_style | py::array::forcecast> offsets,
    py::array_t<std::uint32_t, py::array::c_style | py::array::forcecast> lengths,
    double scale
) {
    if (scale <= 0.0) {
        throw std::runtime_error("ms1_collect_full_mz_qdelta scale must be positive");
    }
    auto mz_buf = mz_data.request();
    auto offset_buf = offsets.request();
    auto length_buf = lengths.request();
    if (mz_buf.ndim != 1 || offset_buf.ndim != 1 || length_buf.ndim != 1) {
        throw std::runtime_error("ms1_collect_full_mz_qdelta expects 1D arrays");
    }
    const auto n_scans = static_cast<std::size_t>(length_buf.shape[0]);
    if (static_cast<std::size_t>(offset_buf.shape[0]) != n_scans) {
        throw std::runtime_error("ms1_collect_full_mz_qdelta offset/length mismatch");
    }
    const auto n_mz = static_cast<std::size_t>(mz_buf.shape[0]);
    const auto* mz_ptr = static_cast<const double*>(mz_buf.ptr);
    const auto* offset_ptr = static_cast<const std::uint64_t*>(offset_buf.ptr);
    const auto* length_ptr = static_cast<const std::uint32_t*>(length_buf.ptr);

    std::size_t delta_count = 0;
    for (std::size_t i = 0; i < n_scans; ++i) {
        const std::size_t n = static_cast<std::size_t>(length_ptr[i]);
        if (n > 1U) {
            delta_count += n - 1U;
        }
        const std::uint64_t start = offset_ptr[i];
        if (start > static_cast<std::uint64_t>(n_mz) || start + n > static_cast<std::uint64_t>(n_mz)) {
            throw std::runtime_error("ms1_collect_full_mz_qdelta scan slice out of range");
        }
    }

    auto out_lengths = py::array_t<std::uint32_t>(n_scans);
    auto out_firsts = py::array_t<std::int64_t>(n_scans);
    auto out_deltas = py::array_t<std::int64_t>(delta_count);
    auto* out_lengths_ptr = out_lengths.mutable_data();
    auto* out_firsts_ptr = out_firsts.mutable_data();
    auto* out_deltas_ptr = out_deltas.mutable_data();

    {
        py::gil_scoped_release release;
        std::size_t delta_pos = 0;
        for (std::size_t scan = 0; scan < n_scans; ++scan) {
            const std::size_t n = static_cast<std::size_t>(length_ptr[scan]);
            const std::size_t start = static_cast<std::size_t>(offset_ptr[scan]);
            out_lengths_ptr[scan] = length_ptr[scan];
            if (n == 0U) {
                out_firsts_ptr[scan] = 0;
                continue;
            }
            std::int64_t prev = static_cast<std::int64_t>(std::llrint(mz_ptr[start] * scale));
            out_firsts_ptr[scan] = prev;
            for (std::size_t i = 1; i < n; ++i) {
                const std::int64_t curr = static_cast<std::int64_t>(std::llrint(mz_ptr[start + i] * scale));
                out_deltas_ptr[delta_pos++] = curr - prev;
                prev = curr;
            }
        }
    }
    return py::make_tuple(out_lengths, out_firsts, out_deltas);
}

py::tuple ms1_collect_orphan_intensity_sidecar(
    py::array_t<double, py::array::c_style | py::array::forcecast> intensity_data,
    py::array_t<std::uint64_t, py::array::c_style | py::array::forcecast> offsets,
    py::array_t<std::uint32_t, py::array::c_style | py::array::forcecast> lengths,
    py::array_t<std::uint32_t, py::array::c_style | py::array::forcecast> scan_ids,
    py::array_t<std::uint32_t, py::array::c_style | py::array::forcecast> unique_scan_indices,
    py::array_t<std::uint32_t, py::array::c_style | py::array::forcecast> group_starts,
    py::array_t<std::uint32_t, py::array::c_style | py::array::forcecast> group_ends,
    py::array_t<std::uint32_t, py::array::c_style | py::array::forcecast> order,
    py::array_t<std::uint32_t, py::array::c_style | py::array::forcecast> covered_starts,
    py::array_t<std::uint32_t, py::array::c_style | py::array::forcecast> covered_counts
) {
    auto intensity_buf = intensity_data.request();
    auto offset_buf = offsets.request();
    auto length_buf = lengths.request();
    auto scan_id_buf = scan_ids.request();
    auto unique_buf = unique_scan_indices.request();
    auto group_start_buf = group_starts.request();
    auto group_end_buf = group_ends.request();
    auto order_buf = order.request();
    auto covered_start_buf = covered_starts.request();
    auto covered_count_buf = covered_counts.request();
    if (
        intensity_buf.ndim != 1 || offset_buf.ndim != 1 || length_buf.ndim != 1 ||
        scan_id_buf.ndim != 1 || unique_buf.ndim != 1 || group_start_buf.ndim != 1 ||
        group_end_buf.ndim != 1 || order_buf.ndim != 1 || covered_start_buf.ndim != 1 ||
        covered_count_buf.ndim != 1
    ) {
        throw std::runtime_error("ms1_collect_orphan_intensity_sidecar expects 1D arrays");
    }
    const auto n_scans = static_cast<std::size_t>(length_buf.shape[0]);
    if (
        static_cast<std::size_t>(offset_buf.shape[0]) != n_scans ||
        static_cast<std::size_t>(scan_id_buf.shape[0]) != n_scans
    ) {
        throw std::runtime_error("ms1 orphan scan offset/length/id mismatch");
    }
    const auto n_unique = static_cast<std::size_t>(unique_buf.shape[0]);
    if (
        static_cast<std::size_t>(group_start_buf.shape[0]) != n_unique ||
        static_cast<std::size_t>(group_end_buf.shape[0]) != n_unique
    ) {
        throw std::runtime_error("ms1 orphan covered group mismatch");
    }
    const auto n_runs = static_cast<std::size_t>(covered_start_buf.shape[0]);
    if (
        static_cast<std::size_t>(covered_count_buf.shape[0]) != n_runs ||
        static_cast<std::size_t>(order_buf.shape[0]) != n_runs
    ) {
        throw std::runtime_error("ms1 orphan covered run mismatch");
    }

    const auto n_intensity = static_cast<std::size_t>(intensity_buf.shape[0]);
    const auto* intensity_ptr = static_cast<const double*>(intensity_buf.ptr);
    const auto* offset_ptr = static_cast<const std::uint64_t*>(offset_buf.ptr);
    const auto* length_ptr = static_cast<const std::uint32_t*>(length_buf.ptr);
    const auto* scan_id_ptr = static_cast<const std::uint32_t*>(scan_id_buf.ptr);
    const auto* unique_ptr = static_cast<const std::uint32_t*>(unique_buf.ptr);
    const auto* group_start_ptr = static_cast<const std::uint32_t*>(group_start_buf.ptr);
    const auto* group_end_ptr = static_cast<const std::uint32_t*>(group_end_buf.ptr);
    const auto* order_ptr = static_cast<const std::uint32_t*>(order_buf.ptr);
    const auto* covered_start_ptr = static_cast<const std::uint32_t*>(covered_start_buf.ptr);
    const auto* covered_count_ptr = static_cast<const std::uint32_t*>(covered_count_buf.ptr);

    auto mark_covered = [&](std::vector<std::uint8_t>& covered, std::uint32_t scan_id) {
        if (n_unique == 0U || covered.empty()) {
            return;
        }
        const auto* begin = unique_ptr;
        const auto* end = unique_ptr + n_unique;
        const auto* found = std::lower_bound(begin, end, scan_id);
        if (found == end || *found != scan_id) {
            return;
        }
        const auto group_idx = static_cast<std::size_t>(found - begin);
        const std::size_t gs = static_cast<std::size_t>(group_start_ptr[group_idx]);
        const std::size_t ge = static_cast<std::size_t>(group_end_ptr[group_idx]);
        if (gs > ge || ge > n_runs) {
            throw std::runtime_error("ms1 orphan covered group range out of bounds");
        }
        const std::size_t n = covered.size();
        for (std::size_t pos = gs; pos < ge; ++pos) {
            const std::size_t run_idx = static_cast<std::size_t>(order_ptr[pos]);
            if (run_idx >= n_runs) {
                throw std::runtime_error("ms1 orphan covered run index out of bounds");
            }
            const std::uint64_t rs = static_cast<std::uint64_t>(covered_start_ptr[run_idx]);
            const std::uint64_t rc = static_cast<std::uint64_t>(covered_count_ptr[run_idx]);
            if (rs >= static_cast<std::uint64_t>(n) || rc == 0U) {
                continue;
            }
            const std::uint64_t re64 = std::min(static_cast<std::uint64_t>(n), rs + rc);
            for (std::uint64_t j = rs; j < re64; ++j) {
                covered[static_cast<std::size_t>(j)] = 1U;
            }
        }
    };

    std::size_t total_orphans = 0;
    {
        py::gil_scoped_release release;
        std::vector<std::uint8_t> covered;
        for (std::size_t scan = 0; scan < n_scans; ++scan) {
            const std::size_t n = static_cast<std::size_t>(length_ptr[scan]);
            const std::uint64_t start64 = offset_ptr[scan];
            if (start64 > static_cast<std::uint64_t>(n_intensity) || start64 + n > static_cast<std::uint64_t>(n_intensity)) {
                throw std::runtime_error("ms1 orphan scan slice out of range");
            }
            if (n == 0U) {
                continue;
            }
            covered.assign(n, 0U);
            mark_covered(covered, scan_id_ptr[scan]);
            const std::size_t start = static_cast<std::size_t>(start64);
            for (std::size_t j = 0; j < n; ++j) {
                if (intensity_ptr[start + j] != 0.0 && covered[j] == 0U) {
                    ++total_orphans;
                }
            }
        }
    }

    auto out_scan_indices = py::array_t<std::uint32_t>(total_orphans);
    auto out_array_indices = py::array_t<std::uint32_t>(total_orphans);
    auto out_intensities = py::array_t<double>(total_orphans);
    auto* out_scan_ptr = out_scan_indices.mutable_data();
    auto* out_array_ptr = out_array_indices.mutable_data();
    auto* out_intensity_ptr = out_intensities.mutable_data();

    {
        py::gil_scoped_release release;
        std::vector<std::uint8_t> covered;
        std::size_t out_pos = 0;
        for (std::size_t scan = 0; scan < n_scans; ++scan) {
            const std::size_t n = static_cast<std::size_t>(length_ptr[scan]);
            const std::size_t start = static_cast<std::size_t>(offset_ptr[scan]);
            if (n == 0U) {
                continue;
            }
            covered.assign(n, 0U);
            mark_covered(covered, scan_id_ptr[scan]);
            for (std::size_t j = 0; j < n; ++j) {
                const double value = intensity_ptr[start + j];
                if (value != 0.0 && covered[j] == 0U) {
                    if (out_pos >= total_orphans) {
                        throw std::runtime_error("ms1 orphan output overflow");
                    }
                    out_scan_ptr[out_pos] = scan_id_ptr[scan];
                    out_array_ptr[out_pos] = static_cast<std::uint32_t>(j);
                    out_intensity_ptr[out_pos] = value;
                    ++out_pos;
                }
            }
        }
        if (out_pos != total_orphans) {
            throw std::runtime_error("ms1 orphan output count mismatch");
        }
    }

    return py::make_tuple(out_scan_indices, out_array_indices, out_intensities);
}

PYBIND11_MODULE(_cross_scan_speedups, m) {
    m.doc() = "Optional pybind11 speedups for TrackCodec MS1 cross-scan codec";
    m.def("zero_rle_encode_uint32", &zero_rle_encode_uint32, py::arg("arr"), py::arg("min_run") = 4);
    m.def("zero_rle_decode_uint32", &zero_rle_decode_uint32);
    m.def("pack_small_uint", &pack_small_uint);
    m.def("unpack_small_uint", &unpack_small_uint);
    m.def("pack_uint32_bits", &pack_uint32_bits);
    m.def("unpack_uint32_bits", &unpack_uint32_bits);
    m.def("estimate_uint32_bitpack_bytes", &estimate_uint32_bitpack_bytes);
    m.def("estimate_signed_residual_bytes", &estimate_signed_residual_bytes);
    m.def("estimate_raw_mz_local_bytes", &estimate_raw_mz_local_bytes);
    m.def("estimate_int64_main_bytes", &estimate_int64_main_bytes);
    m.def("estimate_delta_int64_bytes", &estimate_delta_int64_bytes);
    m.def("estimate_delta2_int64_bytes", &estimate_delta2_int64_bytes);
    m.def("encode_signed_int64_to_int32_main", &encode_signed_int64_to_int32_main);
    m.def("strategy_a_island_features", &strategy_a_island_features);
    m.def("strategy_b_island_features", &strategy_b_island_features);
    m.def("build_compact_tracks_baseline", &build_compact_tracks_baseline);
    m.def("best_delta_reference_equal_fidelity", &best_delta_reference_equal_fidelity);
    m.def("best_cross_track_reference_equal_fidelity", &best_cross_track_reference_equal_fidelity);
    m.def("plan_track_intensity_equal_fidelity", &plan_track_intensity_equal_fidelity);
    m.def("best_residual_mz_model", &best_residual_mz_model);
    m.def("select_mz_representation_u64", &select_mz_representation_u64);
    m.def("build_representation_compact_views", &build_representation_compact_views);
    m.def("fill_equal_fidelity_intensity_streams_compact_views", &fill_equal_fidelity_intensity_streams_compact_views);
    m.def("fill_strict_lossless_intensity_streams_compact_views", &fill_strict_lossless_intensity_streams_compact_views);
    m.def("ms1_expand_equal_fidelity_intensity_by_scan_f64", &ms1_expand_equal_fidelity_intensity_by_scan_f64);
    m.def("ms1_expand_float64_intensity_by_scan_f64", &ms1_expand_float64_intensity_by_scan_f64);
    m.def("ms1_expand_equal_fidelity_island_intensity_f64", &ms1_expand_equal_fidelity_island_intensity_f64);
    m.def("ms1_expand_float64_island_intensity_f64", &ms1_expand_float64_island_intensity_f64);
    m.def("ms2_exact_track_collect_entries", &ms2_exact_track_collect_entries);
    m.def("ms2_exact_track_build_follow_deltas", &ms2_exact_track_build_follow_deltas);
    m.def("ms2_select_top_ids", &ms2_select_top_ids);
    m.def("ms2_exact_track_expand_by_scan", &ms2_exact_track_expand_by_scan);
    m.def("ms2_exact_track_expand_by_scan_f64", &ms2_exact_track_expand_by_scan_f64);
    m.def("ms1_collect_full_mz_qdelta", &ms1_collect_full_mz_qdelta);
    m.def("ms1_collect_orphan_intensity_sidecar", &ms1_collect_orphan_intensity_sidecar);
}
