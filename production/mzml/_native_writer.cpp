#include <pybind11/pybind11.h>
#include <pybind11/numpy.h>

#include <algorithm>
#include <atomic>
#include <cctype>
#include <cstdlib>
#include <cstdint>
#include <cstring>
#include <exception>
#include <fstream>
#include <mutex>
#include <stdexcept>
#include <string>
#include <thread>
#include <utility>
#include <vector>
#include <zlib.h>

namespace py = pybind11;

namespace {

constexpr char BASE64_TABLE[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
constexpr size_t INDEX_SCAN_OVERLAP_BYTES = 65536;

std::string base64_encode(const uint8_t* data, size_t len) {
    std::string out;
    out.resize(((len + 2) / 3) * 4);
    size_t i = 0;
    size_t j = 0;
    while (i + 3 <= len) {
        uint32_t v = (static_cast<uint32_t>(data[i]) << 16) |
                     (static_cast<uint32_t>(data[i + 1]) << 8) |
                     static_cast<uint32_t>(data[i + 2]);
        out[j++] = BASE64_TABLE[(v >> 18) & 0x3F];
        out[j++] = BASE64_TABLE[(v >> 12) & 0x3F];
        out[j++] = BASE64_TABLE[(v >> 6) & 0x3F];
        out[j++] = BASE64_TABLE[v & 0x3F];
        i += 3;
    }
    if (i < len) {
        uint32_t v = static_cast<uint32_t>(data[i]) << 16;
        bool has_second = (i + 1 < len);
        if (has_second) {
            v |= static_cast<uint32_t>(data[i + 1]) << 8;
        }
        out[j++] = BASE64_TABLE[(v >> 18) & 0x3F];
        out[j++] = BASE64_TABLE[(v >> 12) & 0x3F];
        out[j++] = has_second ? BASE64_TABLE[(v >> 6) & 0x3F] : '=';
        out[j++] = '=';
    }
    return out;
}

std::vector<uint8_t> zlib_compress_default(const uint8_t* data, size_t len) {
    uLongf bound = compressBound(static_cast<uLong>(len));
    std::vector<uint8_t> out(bound);
    int rc = compress2(out.data(), &bound, data, static_cast<uLong>(len), Z_DEFAULT_COMPRESSION);
    if (rc != Z_OK) {
        throw std::runtime_error("zlib compress2 failed with code " + std::to_string(rc));
    }
    out.resize(static_cast<size_t>(bound));
    return out;
}

template <typename OutT>
std::vector<uint8_t> copy_cast_array(py::array arr) {
    py::array_t<OutT, py::array::c_style | py::array::forcecast> typed(arr);
    auto buf = typed.request();
    if (buf.ndim != 1) {
        throw std::runtime_error("encode_float_array expects a 1D array");
    }
    const auto* src = static_cast<const OutT*>(buf.ptr);
    size_t n = static_cast<size_t>(buf.shape[0]);
    std::vector<uint8_t> raw(n * sizeof(OutT));
    std::memcpy(raw.data(), src, raw.size());
    return raw;
}

std::vector<uint8_t> raw_float_bytes(py::array arr, const std::string& dtype_code) {
    if (dtype_code == "f8") {
        return copy_cast_array<double>(arr);
    }
    if (dtype_code == "f4") {
        return copy_cast_array<float>(arr);
    }
    if (dtype_code == "i4") {
        return copy_cast_array<int32_t>(arr);
    }
    if (dtype_code == "i8") {
        return copy_cast_array<int64_t>(arr);
    }
    throw std::runtime_error("unsupported dtype_code for native mzML writer: " + dtype_code);
}

size_t native_batch_threads() {
    const char* raw = std::getenv("TRACKCODEC_NATIVE_MZML_THREADS");
    if (raw == nullptr || raw[0] == '\0') {
        return 1;
    }
    try {
        long parsed = std::stol(raw);
        if (parsed <= 1) {
            return 1;
        }
        return static_cast<size_t>(parsed);
    } catch (...) {
        return 1;
    }
}

std::string encode_raw_bytes(const std::vector<uint8_t>& raw, bool zlib_compress) {
    if (zlib_compress) {
        std::vector<uint8_t> compressed = zlib_compress_default(raw.data(), raw.size());
        return base64_encode(compressed.data(), compressed.size());
    }
    return base64_encode(raw.data(), raw.size());
}

py::str encode_float_array(py::array arr, const std::string& dtype_code, bool zlib_compress) {
    std::vector<uint8_t> raw = raw_float_bytes(arr, dtype_code);
    return py::str(encode_raw_bytes(raw, zlib_compress));
}

py::tuple encode_float_array_pair(
    py::array first,
    const std::string& first_dtype_code,
    bool first_zlib_compress,
    py::array second,
    const std::string& second_dtype_code,
    bool second_zlib_compress
) {
    std::vector<uint8_t> first_raw = raw_float_bytes(first, first_dtype_code);
    std::vector<uint8_t> second_raw = raw_float_bytes(second, second_dtype_code);
    std::string first_encoded;
    std::string second_encoded;
    {
        py::gil_scoped_release release;
        first_encoded = encode_raw_bytes(first_raw, first_zlib_compress);
        second_encoded = encode_raw_bytes(second_raw, second_zlib_compress);
    }
    return py::make_tuple(py::str(first_encoded), py::str(second_encoded));
}

py::list encode_float_array_pairs_batch(
    py::sequence first_arrays,
    const std::string& first_dtype_code,
    bool first_zlib_compress,
    py::sequence second_arrays,
    const std::string& second_dtype_code,
    bool second_zlib_compress
) {
    const py::ssize_t n = static_cast<py::ssize_t>(py::len(first_arrays));
    if (static_cast<py::ssize_t>(py::len(second_arrays)) != n) {
        throw std::runtime_error("encode_float_array_pairs_batch array count mismatch");
    }

    std::vector<std::vector<uint8_t>> first_raw;
    std::vector<std::vector<uint8_t>> second_raw;
    first_raw.reserve(static_cast<size_t>(n));
    second_raw.reserve(static_cast<size_t>(n));
    for (ssize_t i = 0; i < n; ++i) {
        first_raw.push_back(raw_float_bytes(py::cast<py::array>(first_arrays[i]), first_dtype_code));
        second_raw.push_back(raw_float_bytes(py::cast<py::array>(second_arrays[i]), second_dtype_code));
    }

    std::vector<std::pair<std::string, std::string>> encoded;
    encoded.resize(static_cast<size_t>(n));
    {
        py::gil_scoped_release release;
        const size_t count = static_cast<size_t>(n);
        size_t threads = std::min(native_batch_threads(), count);
        if (threads <= 1 || count <= 1) {
            for (size_t i = 0; i < count; ++i) {
                encoded[i].first = encode_raw_bytes(first_raw[i], first_zlib_compress);
                encoded[i].second = encode_raw_bytes(second_raw[i], second_zlib_compress);
            }
        } else {
            std::atomic<size_t> next{0};
            std::exception_ptr first_error = nullptr;
            std::mutex error_mutex;
            auto worker = [&]() {
                while (true) {
                    size_t i = next.fetch_add(1);
                    if (i >= count) {
                        break;
                    }
                    try {
                        encoded[i].first = encode_raw_bytes(first_raw[i], first_zlib_compress);
                        encoded[i].second = encode_raw_bytes(second_raw[i], second_zlib_compress);
                    } catch (...) {
                        std::lock_guard<std::mutex> lock(error_mutex);
                        if (first_error == nullptr) {
                            first_error = std::current_exception();
                        }
                    }
                }
            };
            std::vector<std::thread> pool;
            pool.reserve(threads);
            for (size_t t = 0; t < threads; ++t) {
                pool.emplace_back(worker);
            }
            for (auto& thread : pool) {
                thread.join();
            }
            if (first_error != nullptr) {
                std::rethrow_exception(first_error);
            }
        }
    }

    py::list out;
    for (ssize_t i = 0; i < n; ++i) {
        out.append(py::make_tuple(py::str(encoded[static_cast<size_t>(i)].first), py::str(encoded[static_cast<size_t>(i)].second)));
    }
    return out;
}

std::string py_bytes_or_str_to_string(const py::object& obj) {
    if (py::isinstance<py::bytes>(obj)) {
        return py::cast<std::string>(obj);
    }
    if (py::isinstance<py::str>(obj)) {
        return py::cast<std::string>(obj);
    }
    throw std::runtime_error("expected bytes or str");
}

bool is_name_char(unsigned char ch) {
    return std::isalnum(ch) || ch == '_' || ch == '-' || ch == '.';
}

bool tag_name_matches_at(const std::string& data, size_t pos, const std::string& tag) {
    if (pos >= data.size() || data[pos] != '<') {
        return false;
    }
    size_t name_start = pos + 1;
    if (name_start >= data.size() || data[name_start] == '/' || data[name_start] == '!' || data[name_start] == '?') {
        return false;
    }
    size_t name_end = name_start;
    while (name_end < data.size()) {
        const unsigned char ch = static_cast<unsigned char>(data[name_end]);
        if (!(is_name_char(ch) || ch == ':')) {
            break;
        }
        ++name_end;
    }
    if (name_end <= name_start) {
        return false;
    }
    size_t local_start = name_start;
    for (size_t i = name_start; i < name_end; ++i) {
        if (data[i] == ':') {
            local_start = i + 1;
        }
    }
    return (name_end - local_start == tag.size()) && data.compare(local_start, tag.size(), tag) == 0;
}

std::string extract_id_attr(const std::string& open_tag) {
    size_t pos = 0;
    while (true) {
        pos = open_tag.find("id=\"", pos);
        if (pos == std::string::npos) {
            return std::string();
        }
        if (pos == 0) {
            pos += 4;
            continue;
        }
        const unsigned char prev = static_cast<unsigned char>(open_tag[pos - 1]);
        if (std::isspace(prev)) {
            const size_t value_start = pos + 4;
            const size_t value_end = open_tag.find('"', value_start);
            if (value_end == std::string::npos) {
                return std::string();
            }
            return open_tag.substr(value_start, value_end - value_start);
        }
        pos += 4;
    }
}

void scan_open_tags(
    const std::string& data,
    size_t process_limit,
    int64_t data_start,
    const std::string& tag,
    int64_t base_offset,
    int64_t& last_emitted_abs,
    std::vector<std::pair<std::string, int64_t>>& out
) {
    size_t pos = 0;
    while (pos < process_limit) {
        pos = data.find('<', pos);
        if (pos == std::string::npos || pos >= process_limit) {
            break;
        }
        if (!tag_name_matches_at(data, pos, tag)) {
            ++pos;
            continue;
        }
        const size_t tag_end = data.find('>', pos);
        if (tag_end == std::string::npos) {
            break;
        }
        const int64_t abs_pos = data_start + static_cast<int64_t>(pos);
        if (abs_pos > last_emitted_abs) {
            const std::string open_tag = data.substr(pos, tag_end - pos + 1);
            const std::string id = extract_id_attr(open_tag);
            if (!id.empty()) {
                out.emplace_back(id, base_offset + abs_pos);
                last_emitted_abs = abs_pos;
            }
        }
        pos = tag_end + 1;
    }
}

void append_index_block(std::string& out, const std::string& name, const std::vector<std::pair<std::string, int64_t>>& pairs) {
    out += "    <index name=\"";
    out += name;
    out += "\">\n";
    for (const auto& item : pairs) {
        out += "      <offset idRef=\"";
        out += item.first;
        out += "\">";
        out += std::to_string(item.second);
        out += "</offset>\n";
    }
    out += "    </index>\n";
}

void write_all(std::ofstream& out, const std::string& data) {
    if (!data.empty()) {
        out.write(data.data(), static_cast<std::streamsize>(data.size()));
        if (!out) {
            throw std::runtime_error("failed writing indexed mzML output");
        }
    }
}

py::dict write_indexed_mzml_from_body(
    const std::string& body_path,
    const std::string& output_path,
    py::object xml_decl_obj,
    py::object indexed_open_obj,
    py::object indexed_close_obj,
    int64_t mzml_base_offset,
    int64_t expected_spectrum_count,
    int64_t expected_chromatogram_count
) {
    const std::string xml_decl = py_bytes_or_str_to_string(xml_decl_obj);
    const std::string indexed_open = py_bytes_or_str_to_string(indexed_open_obj);
    const std::string indexed_close = py_bytes_or_str_to_string(indexed_close_obj);

    std::ifstream body(body_path, std::ios::binary);
    if (!body) {
        throw std::runtime_error("failed opening mzML body file for native finalizer: " + body_path);
    }
    std::ofstream out(output_path, std::ios::binary | std::ios::trunc);
    if (!out) {
        throw std::runtime_error("failed opening native indexed mzML output file: " + output_path);
    }

    int64_t bytes_written = 0;
    auto counted_write = [&](const std::string& data) {
        write_all(out, data);
        bytes_written += static_cast<int64_t>(data.size());
    };

    counted_write(xml_decl);
    counted_write(indexed_open);
    counted_write("\n");

    std::vector<std::pair<std::string, int64_t>> spectrum_offsets;
    std::vector<std::pair<std::string, int64_t>> chromatogram_offsets;
    std::string tail;
    std::vector<char> buffer(8 * 1024 * 1024);
    int64_t file_offset = 0;
    int64_t last_spectrum_abs = -1;
    int64_t last_chromatogram_abs = -1;

    while (true) {
        body.read(buffer.data(), static_cast<std::streamsize>(buffer.size()));
        const std::streamsize got = body.gcount();
        const bool eof = got <= 0;
        std::string chunk;
        if (got > 0) {
            chunk.assign(buffer.data(), static_cast<size_t>(got));
            counted_write(chunk);
        }

        std::string data;
        data.reserve(tail.size() + chunk.size());
        data += tail;
        data += chunk;
        const int64_t data_start = file_offset - static_cast<int64_t>(tail.size());
        size_t process_limit = 0;
        if (eof) {
            process_limit = data.size();
        } else if (data.size() > INDEX_SCAN_OVERLAP_BYTES) {
            process_limit = data.size() - INDEX_SCAN_OVERLAP_BYTES;
        }

        if (process_limit > 0) {
            scan_open_tags(
                data,
                process_limit,
                data_start,
                "spectrum",
                mzml_base_offset,
                last_spectrum_abs,
                spectrum_offsets
            );
            scan_open_tags(
                data,
                process_limit,
                data_start,
                "chromatogram",
                mzml_base_offset,
                last_chromatogram_abs,
                chromatogram_offsets
            );
        }

        if (eof) {
            break;
        }
        tail = data.substr(process_limit);
        file_offset += static_cast<int64_t>(got);
    }
    if (!body.eof()) {
        throw std::runtime_error("failed reading mzML body file in native finalizer");
    }

    if (expected_spectrum_count >= 0 && static_cast<int64_t>(spectrum_offsets.size()) != expected_spectrum_count) {
        throw std::runtime_error("native indexedmzML spectrum offset count mismatch");
    }
    if (expected_chromatogram_count >= 0 && static_cast<int64_t>(chromatogram_offsets.size()) != expected_chromatogram_count) {
        throw std::runtime_error("native indexedmzML chromatogram offset count mismatch");
    }

    counted_write("\n");
    const int64_t index_list_offset_value = bytes_written;
    const int64_t index_count = 1 + (chromatogram_offsets.empty() ? 0 : 1);
    std::string index_list;
    index_list += "<indexList count=\"";
    index_list += std::to_string(index_count);
    index_list += "\">\n";
    append_index_block(index_list, "spectrum", spectrum_offsets);
    if (!chromatogram_offsets.empty()) {
        append_index_block(index_list, "chromatogram", chromatogram_offsets);
    }
    index_list += "</indexList>\n";
    counted_write(index_list);
    counted_write("  <indexListOffset>" + std::to_string(index_list_offset_value) + "</indexListOffset>\n");
    counted_write("  <fileChecksum>0</fileChecksum>\n");
    counted_write(indexed_close);
    out.close();
    if (!out) {
        throw std::runtime_error("failed finalizing native indexed mzML output");
    }

    py::dict result;
    result["spectrum_count"] = py::int_(spectrum_offsets.size());
    result["chromatogram_count"] = py::int_(chromatogram_offsets.size());
    result["index_list_offset"] = py::int_(index_list_offset_value);
    result["bytes_written"] = py::int_(bytes_written);
    return result;
}

}  // namespace

PYBIND11_MODULE(_native_writer, m) {
    m.doc() = "Native mzML binary array zlib/base64 writer helpers for TrackCodec";
    m.def("encode_float_array", &encode_float_array, py::arg("array"), py::arg("dtype_code"), py::arg("zlib_compress"));
    m.def(
        "encode_float_array_pair",
        &encode_float_array_pair,
        py::arg("first"),
        py::arg("first_dtype_code"),
        py::arg("first_zlib_compress"),
        py::arg("second"),
        py::arg("second_dtype_code"),
        py::arg("second_zlib_compress")
    );
    m.def(
        "encode_float_array_pairs_batch",
        &encode_float_array_pairs_batch,
        py::arg("first_arrays"),
        py::arg("first_dtype_code"),
        py::arg("first_zlib_compress"),
        py::arg("second_arrays"),
        py::arg("second_dtype_code"),
        py::arg("second_zlib_compress")
    );
    m.def(
        "write_indexed_mzml_from_body",
        &write_indexed_mzml_from_body,
        py::arg("body_path"),
        py::arg("output_path"),
        py::arg("xml_decl"),
        py::arg("indexed_open"),
        py::arg("indexed_close"),
        py::arg("mzml_base_offset"),
        py::arg("expected_spectrum_count") = -1,
        py::arg("expected_chromatogram_count") = -1
    );
}
