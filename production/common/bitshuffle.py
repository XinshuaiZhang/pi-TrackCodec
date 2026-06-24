"""Bitshuffle and Byteplane preprocessing for float64 residuals"""
import numpy as np


def bitshuffle_encode(data):
    """
    Bitshuffle: reorder bytes so that similar bits cluster together.
    For a float64 array, group byte 0 of every value together, byte 1 together, ...
    """
    if len(data) == 0:
        return data

    arr = np.ascontiguousarray(data)
    byte_view = arr.view(np.uint8).reshape(len(arr), arr.itemsize)
    return np.ascontiguousarray(byte_view.T).tobytes()


def bitshuffle_decode(shuffled_bytes, dtype, n_elements):
    """Decode bitshuffle."""
    element_size = np.dtype(dtype).itemsize
    expected_size = int(n_elements) * element_size
    if len(shuffled_bytes) != expected_size:
        raise ValueError(f"Bitshuffle payload size mismatch: expected {expected_size}, got {len(shuffled_bytes)}")
    planes = np.frombuffer(shuffled_bytes, dtype=np.uint8).reshape(element_size, int(n_elements))
    original = np.ascontiguousarray(planes.T)
    return np.frombuffer(original.tobytes(), dtype=dtype)


def byteplane_split(data):
    """
    Byteplane: split a float64 array into 8 byte planes.
    Each plane is compressed separately.
    """
    arr = np.ascontiguousarray(data)
    byte_view = arr.view(np.uint8).reshape(len(arr), arr.itemsize)
    return [np.ascontiguousarray(byte_view[:, byte_pos]).tobytes() for byte_pos in range(arr.itemsize)]


def byteplane_merge(planes, dtype=np.float64):
    """
    Inverse of byteplane_split: reassemble byte planes back into array bytes.

    Args:
        planes: list of bytes, one per byte position
        dtype: target numpy dtype

    Returns:
        bytes that can be cast to np.frombuffer(result, dtype=dtype)
    """
    element_size = np.dtype(dtype).itemsize
    if len(planes) != element_size:
        raise ValueError(f"Expected {element_size} planes for {dtype}, got {len(planes)}")

    n_elements = len(planes[0])
    for byte_pos, plane in enumerate(planes):
        if len(plane) != n_elements:
            raise ValueError(
                f"Byteplane length mismatch at plane {byte_pos}: expected {n_elements}, got {len(plane)}"
            )
    plane_matrix = np.vstack([np.frombuffer(plane, dtype=np.uint8) for plane in planes])
    return np.ascontiguousarray(plane_matrix.T).tobytes()
